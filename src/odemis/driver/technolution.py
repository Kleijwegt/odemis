# -*- coding: utf-8 -*-
'''
Created on 11 May 2020

@author: Sabrina Rossberger, Kornee Kleijwegt

Copyright © 2019-2020 Kornee Kleijwegt, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Driver/wrapper for the ASP API in Odemis which can connect Odemis to the ASM server of Technolution of the
# multi-beam project
from __future__ import division

import math
import numpy
import base64
import json
import logging
import queue
import re
import signal
import threading
import time
from PIL import Image
from io import BytesIO
# TODO Add requests module to project requirements
from urllib.parse import urlparse, urlunparse

from requests import Session
from scipy import signal

from odemis import model
from odemis.model import HwError
from odemis.util import almost_equal

from openapi_server.models.field_meta_data import FieldMetaData
from openapi_server.models.mega_field_meta_data import MegaFieldMetaData
from openapi_server.models.cell_parameters import CellParameters
from openapi_server.models.calibration_loop_parameters import CalibrationLoopParameters

DATA_CONTENT_TO_ASM = {"empty": None, "thumbnail": True, "full": False}


class AcquisitionServer(model.HwComponent):
    """
    Component representing the Acquisition server module which is connected via the ASM API. This module controls the
    camera (mppc sensor) for acquiring the image data. It is also connected to the Scan and Acquisition module (SAM),
    which triggers the scanner on the SEM to move the electron beam. Moreover it controls the de-scanner which counter
    scans the scanner movement to ensure that the collected signal always hits the center of each mppc cell on the
    detector.
    """

    def __init__(self, name, role, host, children={}, externalStorage={}, **kwargs):
        """
        Initialize the Acquisition server and the connection with the ASM API.

        :param name (str): Name of the component
        :param role (str): Role of the component
        :param host (str): URL of the host (ASM)
        :param children (dict): dictionary containing HW components and there respective configuration
        :param kwargs:
        """

        super(AcquisitionServer, self).__init__(name, role, **kwargs)

        self._host = host
        # Use session object avoids creating a new connection for each message sent
        # (note: Session() auto-reconnects if the connection is broken for a new call)
        self._session = Session()

        # Try (3 times) the connection with the host and stop any acquisition if already one was in progress
        for i in range(1, 4):
            try:
                self.ASM_API_Post_Call("/scan/finish_mega_field", 204)
                break
            except Exception as error:
                logging.warning("Try number %s of establishing a connecting with the ASM host failed.\n"
                                "Received the error: \n\t %s" % (i, error))
        else:
            logging.error("Could not connect with the ASM host.\n"
                          "Check if the connection with the host is available and if the host URL is entered "
                          "correctly.")
            raise HwError("Could not connect with the ASM host.\n"
                          "Check if the connection with the host is available and if the host URL is entered "
                          "correctly.")

        # NOTE: Do not write real username/password here since this is published on github in plaintext!
        # example = ftp://username:password@example.com/Pictures
        self.externalStorageURL = model.StringVA('ftp://%s:%s@%s.com/%s' %
                                                 (externalStorage["username"],
                                                  externalStorage["password"],
                                                  externalStorage["host"],
                                                  externalStorage["directory"]),
                                                 setter=self._setURL)
        # VA's for calibration loop
        self.calibrationMode = model.BooleanVA(False, setter=self._setCalibrationMode)

        # CalibrationParameters holds the current calibration parameters (required for testing and prevents nasty
        # debug because the API gives only minimal feedback on providing wrong parameters)
        self._calibrationParameters = None

        self.ASM_API_Post_Call("/config/set_external_storage?host=%s&user=%s&password=%s" %
                               (urlparse(self.externalStorageURL.value).hostname,
                                urlparse(self.externalStorageURL.value).username,
                                urlparse(self.externalStorageURL.value).password), 204)
        self.ASM_API_Post_Call("/config/set_system_sw_name?software=%s" % name, 204)

        # Setup hw and sw version
        # TODO make call set_system_sw_name to new simulator (if implemented)
        self._swVersion = "PUT NEW SIMULATOR DATA HERE"
        self._hwVersion = "PUT NEW SIMULATOR DATA HERE"

        # Order of initialisation matters due to dependency of VA's and variables in between children.
        try:
            ckwargs = children["EBeamScanner"]
        except Exception:
            raise ValueError("Required child EBeamScanner not provided")
        self._ebeam_scanner = EBeamScanner(parent=self, **ckwargs)
        self.children.value.add(self._ebeam_scanner)

        try:
            ckwargs = children["MirrorDescanner"]
        except Exception:
            raise ValueError("Required child MirrorDescanner not provided")
        self._mirror_descanner = MirrorDescanner(parent=self, **ckwargs)
        self.children.value.add(self._mirror_descanner)

        try:
            ckwargs = children["MPPC"]
        except Exception:
            raise ValueError("Required child MPPC not provided")
        self._mppc = MPPC(parent=self, **ckwargs)
        self.children.value.add(self._mppc)

    def terminate(self):
        """
        Stops the calibration method, calls the terminate command on all the children,
         and closes the connection (via the request session) to the ASM.
        """
        self._stopCalibration()
        # terminate children
        for child in self.children.value:
            child.terminate()
        self._session.close()

    def ASM_API_Get_Call(self, url, expected_status, data=None, raw_response=False, timeout=600, **kwargs):
        """
        Call to the ASM API to get data from the ASM API

        :param url (str): url of the command, server part is defined in object variable self._host
        :param expected_status (int): expected feedback of server for a successful call
        :param data: data (request body) added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response (bool): Specifies the format of the structure returned. For not raw (False) the content
        of the response is translated from json and returned. Otherwise the entire response is returned.
        :param timeout (int): [s] if within this period no bytes are received an timeout exception is raised
        :return: translate content from the response, or entire response (raw_response=True)
        """
        logging.debug("Executing GET: %s" % url)
        resp = self._session.get(self._host + url, json=data, timeout=timeout, **kwargs)

        if resp.status_code != expected_status:
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list):
                        try:
                            # Limit to output of first 10 values to not overload error output message
                            data[key] = "First 10 values of the list:" + str(value[0:10])
                        except:
                            data[key] = "Empty - because data cannot be converted to a string"
            logging.error("Data dictionary used to make call %s holds the keys:\n %s" % (url, str(data)))
            raise AsmApiException(url, resp, expected_status, self)
        if raw_response:
            return resp
        else:
            return json.loads(resp.content)

    def ASM_API_Post_Call(self, url, expected_status, data=None, raw_response=False, timeout=600, **kwargs):
        """
        Call to the ASM API to post data to the ASM API

        :param url (str): url of the command, server part is defined in object variable self._host
        :param expected_status (int): expected feedback of server for a successful call
        :param data: data (request body) added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response (bool): Specifies the format of the structure returned. For not raw (False) the content
        of the response is translated from json and returned. Otherwise the entire response is returned.
        :param timeout (int): [s] if within this period no bytes are received an timeout exception is raised
        :return: status_code(int) or entire response (raw_response=True)
        """
        logging.debug("Executing POST: %s" % url)
        resp = self._session.post(self._host + url, json=data, timeout=timeout, **kwargs)

        if resp.status_code != expected_status:
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list):
                        try:
                            # Limit to output of first 10 values to not overload error output message
                            data[key] = "First 10 values of the list:" + str(value[0:10])
                        except:
                            data[key] = "Empty - because data cannot be converted to a string"
            logging.error("Data dictionary used to make call %s holds the keys:\n %s" % (url, str(data)))
            raise AsmApiException(url, resp, expected_status, self)

        logging.debug("Call to %s was successful.\n" % url)
        if raw_response:
            return resp
        else:
            return resp.status_code

    def checkMegaFieldExists(self, mega_field_id, storage_dir):
        """
        Check if filename complies with set allowed characters
        :param mega_field_id (string): name of the mega field.
        :param storage_dir (string): path to the mega field.
        :return (bool): True if mega field exists.
        """
        ASM_FILE_ILLEGAL_CHARS = r'[^a-z0-9_()-]'
        ASM_PATH_ILLEGAL_CHARS = r'[^A-Za-z0-9/_()-]'
        if not re.search(ASM_PATH_ILLEGAL_CHARS, storage_dir):
            logging.error("The specified storage directory contains invalid characters, cannot check if mega field "
                          "exists (only the characters '%s' are allowed)." % ASM_FILE_ILLEGAL_CHARS[2:-1])
            return False

        if not re.search(ASM_FILE_ILLEGAL_CHARS, mega_field_id):
            logging.error("The specified mega_field_id contains invalid characters, cannot check if mega field exists"
                          "(only the characters '%s' are allowed)." % ASM_FILE_ILLEGAL_CHARS[2:-1])
            return False

        response = self.ASM_API_Post_Call("/scan/check_mega_field?mega_field_id=%s&storage_directory=%s" %
                                          (mega_field_id, storage_dir), 200, raw_response=True)
        return json.loads(response.content)["exists"]

    def _startCalibration(self, *args):
        """
        Call start calibration loop on the ASM and subscribe _startCalibration to all the VA's so the calibration
        loop is restarted, with updated parameters, when one of the VA's is changed.

        *args is need to support self subscribing
        """
        self.ASM_API_Post_Call("/scan/stop_calibration_loop", 204)
        descanner = self._mirror_descanner
        scanner = self._ebeam_scanner
        mppc = self._mppc

        # Retrieving and subscribing the descanner VA's
        descan_rotation = descanner.rotation.value
        descanner.rotation.subscribe(self._startCalibration)
        descan_offset = descanner.scanOffset.value
        descanner.scanOffset.subscribe(self._startCalibration)
        descanner.scanGain.subscribe(self._startCalibration)

        # Retrieving and subscribing the scanner VA's
        dwell_time = scanner.dwellTime.value
        dwell_time_ticks = scanner.getTicksDwellTime()
        scanner.dwellTime.subscribe(self._startCalibration)
        scan_rotation = scanner.rotation.value
        scanner.rotation.subscribe(self._startCalibration)
        scan_delay = scanner.getTicksScanDelay()
        scanner.scanDelay.subscribe(self._startCalibration)
        scan_offset = scanner.scanOffset.value
        scanner.scanOffset.subscribe(self._startCalibration)
        scanner.scanGain.subscribe(self._startCalibration)

        # Retrieving and subscribing the scanner VA
        resolution = mppc.cellCompleteResolution.value[0]
        mppc.cellCompleteResolution.subscribe(self._startCalibration)

        # Check if the descanner clockperiod is still a multiple of de scanner clock period, otherwise raise error.
        if not almost_equal(descanner.clockPeriod.value % scanner.clockPeriod.value, 0):
            logging.error("Descanner and/or scanner clock period changed which means the descanner period is no "
                          "longer a whole multiple of scanner clock periods. This means that calibration is no "
                          "longer accurate.")
            raise ValueError("Descanner clock period is no longer a whole multiple of the scanner clock period.")

        # Creation of setpoints
        flyback_time = descanner.physicalFlybackTime
        remainder_scanning_time = (dwell_time * resolution) % descanner.clockPeriod.value
        if remainder_scanning_time is not 0:
            # Add adjusted flyback time if there is a remainder of scanning time by adding one setpoint to ensure the
            # total scanning period is equal to a whole number of descan clock periods.
            flyback_time = flyback_time + descanner.clockPeriod.value

        # Period of the calibration signal in seconds
        calibration_period = (dwell_time * resolution) + flyback_time

        # Support a callibration freq from 125 to 25000 Hz
        if not scanner.dwellTime.range[0] < calibration_period < 0.008:
            logging.error("Cannot perform calibration using a calibration frequency lower than 125 or 5000. With the "
                          "given parameters the required calibration frequency would be %s." % (1/calibration_period))
            raise ValueError("Calibration of given values requires a calibration frequency which is out of range.")
        calibration_frequency = 1/calibration_period

        # One period with points at a sampling period of the descanner clock period
        time_points_descanner = numpy.arange(0, calibration_period, descanner.clockPeriod.value)
        x_descan_setpoints = descanner.scanGain.value[0] * numpy.sin(2 * math.pi * calibration_frequency * time_points_descanner)
        y_descan_setpoints = descanner.scanGain.value[1] * signal.sawtooth(2 * math.pi * calibration_frequency *
                                                                           time_points_descanner)

        # Scan sampling frequency is 9000 times the calibration frequency, meaning 9000 points per calibration period.
        time_points_scanner = numpy.linspace(0, calibration_period, 9000)
        x_scan_setpoints = scanner.scanGain.value[0] * numpy.sin(2 * math.pi * calibration_frequency * time_points_scanner)
        y_scan_setpoints = scanner.scanGain.value[1] * signal.sawtooth(2 * math.pi * calibration_frequency *
                                                                       time_points_scanner)

        callibration_data = CalibrationLoopParameters(descan_rotation,
                                                      descan_offset[0],
                                                      x_descan_setpoints.astype(int).tolist(),
                                                      descan_offset[1],
                                                      y_descan_setpoints.astype(int).tolist(),
                                                      dwell_time_ticks,
                                                      scan_rotation,
                                                      scan_delay[0],
                                                      scan_offset[0],
                                                      x_scan_setpoints.astype(int).tolist(),
                                                      scan_offset[1],
                                                      y_scan_setpoints.astype(int).tolist())

        self._calibrationParameters = callibration_data
        self.ASM_API_Post_Call("/scan/start_calibration_loop", 204, data=callibration_data.to_dict())

    def _stopCalibration(self, *args):
        """
        Call stop calibration loop on the ASM and unsubscribe _startCalibration from all the VA's so the calibration
        loop is not restarted when a VA is changed.

        *args is need to support self subscribing
        """
        # Unsubscribe the descanner VA's
        descanner = self._mirror_descanner
        descanner.scanGain.unsubscribe(self._startCalibration)
        descanner.rotation.unsubscribe(self._startCalibration)
        descanner.scanOffset.unsubscribe(self._startCalibration)

        # Unsubscribe the scanner VA's
        scanner = self._ebeam_scanner
        scanner.scanGain.unsubscribe(self._startCalibration)
        scanner.dwellTime.unsubscribe(self._startCalibration)
        scanner.rotation.unsubscribe(self._startCalibration)
        scanner.scanDelay.unsubscribe(self._startCalibration)
        scanner.scanOffset.unsubscribe(self._startCalibration)

        # Unsubscribe the mppc VA
        self._mppc.cellCompleteResolution.unsubscribe(self._startCalibration)

        self._calibrationParameters = None
        self.ASM_API_Post_Call("/scan/stop_calibration_loop", 204)

    def _setCalibrationMode(self, mode):
        """
        Setter for the calibration mode, this method calls the _startCalibration and _stopCalibration methods
        (responsible for starting/stopping the calibration nd subscriptions to VA's to ensure updated parameters),
        changes the default scanner and descanner gain values for calibration, and unsubscribes all the listeners
        from the mppc.dataflow.

        :param mode (bool):
        """
        if mode:
            if self._mppc.data._count_listeners() != 0:
                logging.warning("Unsubscribing all the listeners from the mppc.dataflow, calibration mode cannot be "
                                "started if the dataflow still has subscribers.")
                while self._mppc.data._count_listeners() != 0:
                    try:
                        self._mppc.data._listeners.pop()
                    except:
                        # If removing one of the listeners fails that is probably due to a lack of listeners
                        break

            # Standard values of scan gain for calibration mode
            self._mirror_descanner.scanGain.value = (1.0, 0.0)
            self._ebeam_scanner.scanGain.value = (1.0, 1.0)
            self._startCalibration()  # Start calibration loop and subscribe to VA's to reset calibration loop
            return True

        else:
            self._stopCalibration()  # Stop calibration loop and unsubscribe from all the VA's
            # Standard values of scan gain for acquisition mode
            self._mirror_descanner.scanGain.value = (1.0, 1.0)
            self._ebeam_scanner.scanGain.value = (1.0, 1.0)
            return False

    def _setURL(self, url):
        """
        Setter which checks for correctness of FTP url_parser and otherwise returns old value.

        :param url(str): e.g. ftp://username:password@example.com
        :return: correct ftp url_parser
        """
        ASM_GENERAL_ILLEGAL_CHARS = r'[^A-Za-z0-9/_()-:@]'
        ASM_USER_ILLEGAL_CHARS = r'[^A-Za-z0-9]'
        ASM_PASSWORD_ILLEGAL_CHARS = r'[^A-Za-z0-9]'
        ASM_HOST_ILLEGAL_CHARS = r'[^A-Za-z0-9.]'
        ASM_PATH_ILLEGAL_CHARS = r'[^A-Za-z0-9/_()-]'

        url_parser = urlparse(url)  # Transform input string to url_parse object

        # Perform general check on valid characters (parses works incorrectly for some invalid characters
        if re.search(ASM_GENERAL_ILLEGAL_CHARS, urlunparse(url_parser)):
            raise ValueError("Invalid character in ftp url is provided, allowed characters are %s placed in the form:"
                             "'ftp://username:password@host_example.com/path/to/Pictures'\n"
                             "(Only use the @ to separate the password and the host." % ASM_GENERAL_ILLEGAL_CHARS[2:-1])

        # Perform detailed checks on input
        if url_parser.scheme != 'ftp' \
                or not url_parser.scheme or not url_parser.username or not url_parser.password \
                or not url_parser.hostname or not url_parser.path:
            # Check both the scheme as well if all sub-elements are non-empty
            # Note that if an extra @ is used (e.g. in the password) the parser works incorrectly and sub-elements
            # are empty after splitting the url input
            raise ValueError("Incorrect ftp url is provided, please use form: "
                             "'ftp://username:password@host_example.com/path/to/Pictures'\n"
                             "(Only use the @ to separate the password and the host.")

        elif re.search(ASM_USER_ILLEGAL_CHARS, url_parser.username):
            raise ValueError(
                    "Username contains invalid characters, username remains unchanged "
                    "(only the characters '%s' are allowed)." % ASM_USER_ILLEGAL_CHARS[2:-1])

        elif re.search(ASM_PASSWORD_ILLEGAL_CHARS, url_parser.password):
            raise ValueError(
                    "Password contains invalid characters, password remains unchanged "
                    "(only the characters '%s' are allowed)." % ASM_PASSWORD_ILLEGAL_CHARS[2:-1])

        elif re.search(ASM_HOST_ILLEGAL_CHARS, url_parser.hostname):
            raise ValueError(
                    "Host contains invalid characters, host remains unchanged "
                    "(only the characters '%s' are allowed)." % ASM_HOST_ILLEGAL_CHARS[2:-1])

        elif re.search(ASM_PATH_ILLEGAL_CHARS, url_parser.path):
            raise ValueError("Path on ftp server contains invalid characters, path remains unchanged "
                             "(only the characters '%s' are allowed)." % ASM_PATH_ILLEGAL_CHARS[2:-1])
        else:
            return url


class EBeamScanner(model.Emitter):
    """
    HW component representing the e-beam scanner.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initialize the e-beam scanner.

        :param name(str): Name of the component
        :param role(str): Role of the component
        :param parent (AcquisitionServer object): Parent object of the component
        """
        super(EBeamScanner, self).__init__(name, role, parent=parent, **kwargs)

        clockFrequencyData = self.parent.ASM_API_Get_Call("/scan/clock_frequency", 200)
        self.clockPeriod = model.FloatVA(1 / clockFrequencyData['frequency'], unit='s', readonly=True)
        self._shape = (8000, 8000)
        self.resolution = model.ResolutionVA((6400, 6400), ((10, 10), (1000 * 8, 1000 * 8)))
        self.dwellTime = model.FloatContinuous(self.clockPeriod.value, (max(self.clockPeriod.value, 4e-7), 1e-4),
                                               unit='s')
        self.pixelSize = model.TupleContinuous((4e-9, 4e-9), range=((1e-9, 1e-9), (1e-3, 1e-3)), unit='m',
                                               setter=self._setPixelSize)
        self.rotation = model.FloatContinuous(0.0, range=(0.0, 2 * math.pi), unit='rad')

        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')
        self.scanGain = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')
        self.scanDelay = model.TupleContinuous((0.0, 0.0), range=((0.0, 0.0), (200e-6, 10.0)), unit='s',
                                               setter=self._setScanDelay)

        self._metadata[model.MD_PIXEL_SIZE] = self.pixelSize.value
        self._metadata[model.MD_DWELL_TIME] = self.dwellTime.value

    def getTicksScanDelay(self):
        """
        :return: Scan delay in number of ticks of the ebeam scanner clock frequency
        """
        return (int(self.scanDelay.value[0] / self.clockPeriod.value),
                int(self.scanDelay.value[1] / self.clockPeriod.value))

    def getTicksDwellTime(self):
        """
        :return: Dwell time in number of ticks of the ebeam scanner clock frequency
        """
        return int(self.dwellTime.value / self.clockPeriod.value)

    def _setPixelSize(self, pixelSize):
        """
        Setter for the pixel size which ensures only square pixel size are entered

        :param pixelSize (tuple):
        :return (tuple):
        """
        if pixelSize[0] == pixelSize[1]:
            return pixelSize
        else:
            logging.warning("Non-square pixel size entered, only square pixel sizes are supported. "
                            "Width of pixel size is used as height.")
            return pixelSize[0], pixelSize[0]

    def _setScanDelay(self, scanDelay):
        """
        Sets the delay for the scanner to start scanning after a mega field acquisition was started/triggered. It is
        checked that the scanner starts scanning before the detector starts recording. Setter which prevents the MPPC
        detector from recording before the ebeam scanner has started.

        :param scanDelay (tuple):
        :return (tuple):
        """
        # Check if detector can record images before ebeam scanner has started to scan.
        if self.parent._mppc.acqDelay.value >= scanDelay[0]:
            return scanDelay
        else:
            # Change Scan Delay value so that the mppc does not start recording before the ebeam scanner has started to
            # scan.
            logging.warning("Detector cannot record images before ebeam scanner has started to scan.\n"
                            "Detector needs to start after scanner.")
            logging.info("The entered acquisition delay is %s in the eBeamScanner and the scan delay in the MPPC is "
                         "%s" % (scanDelay[0], self.parent._mppc.acqDelay.value))
            return self.scanDelay.value


class MirrorDescanner(model.Emitter):
    """
    Represents the Mirror descanner which counter scans the scanner movement to ensure that
    the collected signal always hits the center of each mppc cell on the detector.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initialize the mirror descanner.

        :param name(str): Name of the component
        :param role(str): Role of the component
        :param parent (AcquisitionServer object): Parent object of the component
        """
        super(MirrorDescanner, self).__init__(name, role, parent=parent, **kwargs)

        self.rotation = model.FloatContinuous(0, range=(0, 2 * math.pi), unit='rad')
        self.scanOffset = model.TupleContinuous((4000, 4000), range=((-32768, -32768), (32767, 32767)), unit='V')
        self.scanGain = model.TupleContinuous((10.0, 10.0), range=((-1000.0, -1000.0), (1000.0, 1000.0)), unit='V')

        clockFrequencyData = self.parent.ASM_API_Get_Call("/scan/descan_control_frequency", 200)
        # Check if clockFrequencyData holds the proper key
        if 'frequency' not in clockFrequencyData:
            raise IOError("Could not obtain clock frequency, received data does not hold the proper key")
        self.clockFrequency = clockFrequencyData['frequency']
        self.clockPeriod = model.FloatVA(1 / clockFrequencyData['frequency'], unit='s', readonly=True)

        # TODO check if physicalFlybackTime is constant and what time it should be for EA calibrate --> Wilco & Andries
        # Physical time for the mirror descanner to perform a flyback, assumed constant [s].
        self.physicalFlybackTime = 250e-6

    def getXAcqSetpoints(self):
        """
        Creates the setpoints for the descanner in X direction (used by the ASM) for scanning one row of pixels. The
        X_setpoints describe the movement of the descanner during the scanning of one full row of pixels.  To the ASM
        API one period of setpoints (scanning of one row) is send which is repeated for all following rows.
        A single sawtooth profile (rise and crash) followed by a flyback period (X=0) is used as trajectory. No
        smoothing or low-pas filtering is used to create the trajectory of these setpoints.

        :return: List of ints holding the setpoints in X direction.
        """
        descan_period = self.clockPeriod.value  # in seconds
        dwelltime = self.parent._ebeam_scanner.dwellTime.value  # in seconds
        X_scan_gain = self.scanGain.value[0]
        X_scan_offset = self.scanOffset.value[0]
        X_cell_size = self.parent._mppc.cellCompleteResolution.value[0]  # pixels

        # all units in seconds
        scanning_points = numpy.array(list(map(lambda t: (X_scan_gain / dwelltime) * t,
                                               numpy.arange(0, dwelltime * X_cell_size, descan_period))))

        # Calculation flyback_points is faster but the same as:
        # '0 * numpy.arange(0, self.physicalFlybackTime, descan_period)'
        flyback_points = numpy.zeros(math.ceil(self.physicalFlybackTime / descan_period))

        base = X_scan_offset - 0.5 * X_cell_size * X_scan_gain
        setpoints = base + numpy.concatenate((scanning_points, flyback_points))

        if setpoints.min() < -32768 or setpoints.max() > 32767:
            raise ValueError("Setpoint values are to big/small to be handled by the ASM API.")
        return setpoints.astype(int).tolist()

    def getYAcqSetpoints(self):
        """
        Creates the setpoints for the descanner in Y direction for the ASM.
        During the scanning of a row of pixels the Y value is constant. Only one Y descan setpoint per full row of
        pixels will be read. After completing the scan of a full row of pixels a new Y_setpoints is read which
        describes the movement when the scanner goes from one row to the next row of pixels.

        :return:  List of ints holding the setpoints in Y direction.
        """
        Y_scan_offset = self.scanOffset.value[1]
        Y_scan_gain = self.scanGain.value[1]
        Y_cell_size = self.parent._mppc.cellCompleteResolution.value[1]  # pixels

        first_row_value = Y_scan_offset - 0.5 * Y_cell_size * Y_scan_gain
        last_row_value = Y_scan_offset + 0.5 * Y_cell_size * Y_scan_gain
        setpoints = numpy.arange(first_row_value, last_row_value, Y_scan_gain)

        if len(setpoints) != Y_cell_size:
            raise ValueError("Error in creation of Y_scan setpoints.")

        if setpoints.min() < -32768 or setpoints.max() > 32767:
            raise ValueError("Setpoint values are to big/small to be handled by the ASM API.")
        return setpoints.astype(int).tolist()


class MPPC(model.Detector):
    """
    Represents the camera (mppc sensor) for acquiring the image data.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initializes the camera (mppc sensor) for acquiring the image data.

        :param name(str): Name of the component
        :param role(str): Role of the component
        :param parent (AcquisitionServer object): Parent object of the component
        """
        super(MPPC, self).__init__(name, role, parent=parent, **kwargs)

        # Store siblings on which this class is dependent as attributes
        self._scanner = self.parent._ebeam_scanner
        self._descanner = self.parent._mirror_descanner

        self._shape = (8, 8)
        self.filename = model.StringVA(time.strftime("default--%Y-%m-%d-%H-%M-%S"), setter=self._setFilename)
        self.dataContent = model.StringEnumerated('empty', DATA_CONTENT_TO_ASM.keys())
        self.acqDelay = model.FloatContinuous(0.0, range=(0, 200e-6), unit='s', setter=self._setAcqDelay)

        # Cell acquisition parameters
        self.cellTranslation = model.TupleVA(
                tuple(tuple((50, 50) for i in range(0, self.shape[0])) for i in range(0, self.shape[1])),
                setter=self._setCellTranslation)
        self.cellDarkOffset = model.TupleVA(
                tuple(tuple(0 for i in range(0, self.shape[0])) for i in range(0, self.shape[1]))
                , setter=self._setCellDarkOffset)
        self.cellDigitalGain = model.TupleVA(
                tuple(tuple(1.2 for i in range(0, self.shape[0])) for i in range(0, self.shape[1]))
                , setter=self._setCellDigitalGain)
        self.cellCompleteResolution = model.ResolutionVA((800, 800), ((10, 10), (1000, 1000)))

        # Setup hw and sw version
        # TODO make call set_system_sw_name to new simulator (if implemented in simulator)
        self._swVersion = "PUT NEW SIMULATOR DATA HERE"
        self._hwVersion = "PUT NEW SIMULATOR DATA HERE"

        self._metadata[model.MD_HW_NAME] = "MPPC"
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        self._metadata[model.MD_POS] = (0, 0)  # m

        # Initialize acquisition processes
        self.acq_queue = queue.Queue()  # acquisition queue with commands of actions that need to be executed.
        self._acq_thread = threading.Thread(target=self._acquire, name="acquisition thread")
        self._acq_thread.deamon = True
        self._acq_thread.start()

        self.data = ASMDataFlow(self)

    def terminate(self):
        """
        Terminate acquisition thread and empty the acquisition queue
        """
        super(MPPC, self).terminate()

        # Clear the queue
        while True:
            try:
                self.acq_queue.get(block=False)
            except queue.Empty:
                break

        self.acq_queue.put(("terminate", None))
        self._acq_thread.join(5)

    def _assemble_megafield_metadata(self):
        """
        Gather all the mega field metadata from the VA's and convert to correct format accepted by the ASM API.
        :return: MegaFieldMetaData Model of the ASM API
        """
        stage_position = self._metadata[model.MD_POS]
        cellTranslation = sum(self.cellTranslation.value, ())
        cellDarkOffset = sum(self.cellDarkOffset.value, ())
        cellDigitalGain = sum(self.cellDigitalGain.value, ())
        eff_cell_size = (int(self._scanner.resolution.value[0] / self._shape[0]),
                         int(self._scanner.resolution.value[1] / self._shape[1]))

        scan_to_acq_delay = int((self.acqDelay.value - self._scanner.scanDelay.value[0]) /
                                self._scanner.clockPeriod.value)

        X_descan_setpoints = self._descanner.getXAcqSetpoints()
        Y_descan_setpoints = self._descanner.getYAcqSetpoints()

        megafield_metadata = \
            MegaFieldMetaData(
                    mega_field_id=self.filename.value,
                    storage_directory=urlparse(self.parent.externalStorageURL.value).path,
                    custom_data="No_custom_data",
                    stage_position_x=float(stage_position[0]),
                    stage_position_y=float(stage_position[1]),
                    # Convert pixels size from meters to nanometers
                    pixel_size=int(self._scanner.pixelSize.value[0] * 1e9),
                    dwell_time=self._scanner.getTicksDwellTime(),
                    x_scan_to_acq_delay=scan_to_acq_delay,
                    x_scan_delay=self._scanner.getTicksScanDelay()[0],
                    x_cell_size=self.cellCompleteResolution.value[0],
                    x_eff_cell_size=eff_cell_size[0],
                    y_cell_size=self.cellCompleteResolution.value[1],
                    y_eff_cell_size=eff_cell_size[1],
                    y_prescan_lines=self._scanner.getTicksScanDelay()[1],
                    x_scan_gain=self._scanner.scanGain.value[0],
                    y_scan_gain=self._scanner.scanGain.value[1],
                    x_scan_offset=self._scanner.scanOffset.value[0],
                    y_scan_offset=self._scanner.scanOffset.value[1],
                    # TODO API gives error for values < 0 but YAML does not specify so
                    x_descan_setpoints=X_descan_setpoints,
                    y_descan_setpoints=Y_descan_setpoints,
                    x_descan_offset=self._descanner.scanOffset.value[0],
                    y_descan_offset=self._descanner.scanOffset.value[1],
                    scan_rotation=self._scanner.rotation.value,
                    descan_rotation=self._descanner.rotation.value,
                    cell_parameters=[CellParameters(translation[0], translation[1], darkOffset, digitalGain)
                                     for translation, darkOffset, digitalGain in
                                     zip(cellTranslation, cellDarkOffset, cellDigitalGain)],
            )

        return megafield_metadata

    def _acquire(self):
        """
        Acquisition thread takes input from the acquisition queue (self.acq_queue) which holds a command (for
        starting/stopping acquisition or acquiring a field image; 'start', 'stop','terminate', 'next') and extra
        arguments (MegaFieldMetaData Model or FieldMetaData Model and the notifier function to
        which any return will be redirected)
        """
        try:
            # Prevents acquisitions thread from from starting/performing two acquisitions, or stopping the acquisition
            # twice.
            acquisition_in_progress = None

            while True:
                # Wait until a message is available
                command, *args = self.acq_queue.get(block=True)
                logging.debug("Loaded the command '%s' in the acquisition thread from the acquisition queue." % command)

                if command == "start":
                    if acquisition_in_progress:
                        logging.warning("ASM acquisition already had the '%s', received this command again." % command)
                        continue

                    acquisition_in_progress = True
                    megafield_metadata = args[0]
                    self._metadata = self._mergeMetadata()
                    self.parent.ASM_API_Post_Call("/scan/start_mega_field", 204, megafield_metadata.to_dict())

                elif command == "next":
                    if not acquisition_in_progress:
                        logging.warning("Start ASM acquisition before request to acquire field images.")
                        continue

                    field_data = args[0]  # Field metadata for the specific position of the field to scan
                    dataContent = args[1]  # Specifies the type of image to return (empty, thumbnail or full)
                    notifier_func = args[2]  # Return function (usually, dataflow.notify or acquire_single_field queue)

                    self.parent.ASM_API_Post_Call("/scan/scan_field", 204, field_data.to_dict())

                    if DATA_CONTENT_TO_ASM[dataContent] is None:
                        da = model.DataArray(numpy.array([[0]], dtype=numpy.uint8), metadata=self._metadata)
                    else:
                        # TODO remove time.sleep if the function "waitOnFieldImage" exists. Otherwise the image is
                        #  not yet loaded on the ASM when retrieving it.
                        time.sleep(0.5)
                        resp = self.parent.ASM_API_Get_Call(
                                "/scan/field?x=%d&y=%d&thumbnail=%s" %
                                (field_data.position_x, field_data.position_y,
                                 str(DATA_CONTENT_TO_ASM[dataContent]).lower()),
                                200, raw_response=True, stream=True)
                        resp.raw.decode_content = True  # handle spurious Content-Encoding
                        img = Image.open(BytesIO(base64.b64decode(resp.raw.data)))

                        da = model.DataArray(img, metadata=self._metadata)

                    # Send DA to the function to be notified
                    notifier_func(da)

                elif command == "stop":
                    if not acquisition_in_progress:
                        logging.warning("ASM acquisition was already at status '%s'" % command)
                        continue

                    acquisition_in_progress = False
                    self.parent.ASM_API_Post_Call("/scan/finish_mega_field", 204)

                elif command == "terminate":
                    acquisition_in_progress = None
                    raise TerminationRequested()

                else:
                    logging.error("Received invalid command '%s' is skipped" % command)
                    raise ValueError

        except TerminationRequested:
            logging.info("Terminating acquisition")

        except Exception:
            if command is not None:
                logging.exception("Last message was not executed, should have performed action: '%s'\n"
                                  "Reinitialize and restart the acquisition" % command)
        finally:
            self.parent.ASM_API_Post_Call("/scan/finish_mega_field", 204)
            logging.debug("Acquisition thread ended")

    def start_acquisition(self):
        """
        Put a the command 'start' mega field scan on the queue with the appropriate MegaFieldMetaData Model of the mega
        field image to be scanned. The MegaFieldMetaData is used to setup the HW accordingly, for each field image
        additional field image related metadata is provided.
        """
        if not self._acq_thread or not self._acq_thread.is_alive():
            logging.info('Starting acquisition thread and clearing remainder of the old queue')

            # Clear the queue
            while True:
                try:
                    self.acq_queue.get(block=False)
                except queue.Empty:
                    break

            self._acq_thread = threading.Thread(target=self._acquire,
                                                name="acquisition thread")
            self._acq_thread.deamon = True
            self._acq_thread.start()

        megafield_metadata = self._assemble_megafield_metadata()
        self.acq_queue.put(("start", megafield_metadata))

    def get_next_field(self, field_num):
        '''
        Puts the command 'next' field image scan on the queue with the appropriate field meta data model of the field
        image to be scanned. Can only be executed if it preceded by a 'start' mega field scan command on the queue.
        The acquisition thread returns the acquired image to the provided notifier function added in the acquisition queue
        with the "next" command. As notifier function the dataflow.notify is send. The returned image will be
        returned to the dataflow.notify which will provide the new data to all the subscribers of the dataflow.

        :param field_num(tuple): tuple with x,y coordinates in integers of the field number.
        '''
        field_data = FieldMetaData(*self.convert_field_num2pixels(field_num))
        self.acq_queue.put(("next", field_data, self.dataContent.value, self.data.notify))

    def stop_acquisition(self):
        """
        Puts a 'stop' field image scan on the queue, after this call, no fields can be scanned anymore. A new mega
        field can be started. The call triggers the post processing process to generate and offload additional zoom
        levels.
        """
        self.acq_queue.put(("stop",))

    def cancel_acquistion(self, execution_wait=0.2):
        """
        Clears the entire queue and finished the current acquisition. Does not terminate acquisition thread.
        """
        time.sleep(0.3)  # Wait to make sure noting is being loaded on the queue
        # Clear the queue
        while True:
            try:
                self.acq_queue.get(block=False)
            except queue.Empty:
                break

        self.acq_queue.put(("stop", None))

        if execution_wait > 30:
            logging.error("Failed to cancel the acquisition. MPPC is terminated.")
            self.terminate()
            raise ConnectionError("Connection quality was to low to cancel the acquisition. MPPC is terminated.")

        time.sleep(execution_wait)  # Wait until finish command is executed

        if not self.acq_queue.empty():
            self.cancel_acquistion(execution_wait=execution_wait * 2)  # Let the waiting time increase

    def acquire_single_field(self, dataContent="thumbnail", field_num=(0, 0)):
        """
        Scans a single field image via the acquire thread and the acquisition queue with the appropriate metadata models.
        The function returns this image by providing a return_queue to the acquisition thread. The use of this queue
        allows the use of the timeout functionality of a queue to prevent waiting to long on a return image (
        timeout=600 seconds).

        :param dataContent (string): Can be either: "empty", "thumbnail", "full"
        :param field_num (tuple): x,y integer number, location of the field number with the metadata provided.
        :return: DA of the single field image
        """
        if dataContent not in DATA_CONTENT_TO_ASM:
            logging.warning("Incorrect dataContent provided for acquiring a single image, thumbnail is used as default "
                            "instead.")
            dataContent = "thumbnail"

        return_queue = queue.Queue()  # queue which allows to return images and be blocked when waiting on images
        mega_field_data = self._assemble_megafield_metadata()

        self.acq_queue.put(("start", mega_field_data))
        field_data = FieldMetaData(*self.convert_field_num2pixels(field_num))

        self.acq_queue.put(("next", field_data, dataContent, return_queue.put))
        self.acq_queue.put(("stop",))

        return return_queue.get(timeout=600)

    def convert_field_num2pixels(self, field_num):
        """

        :param field_num(tuple): tuple with x,y coordinates in integers of the field number.
        :return: field number (tuple of ints)
        """
        return (field_num[0] * self._scanner.resolution.value[0],
                field_num[1] * self._scanner.resolution.value[1])

    def getTicksAcqDelay(self):
        """
        :return: Acq delay in number of ticks of the ebeam scanner clock frequency
        """
        return int(self.acqDelay.value / self._scanner.clockPeriod.value)

    def _mergeMetadata(self):
        """
        Create dict containing all metadata from siblings and own metadata
        """
        md = {}
        self._metadata[model.MD_ACQ_DATE] = time.time()  # Time since Epoch

        # Gather metadata from all related HW components and own _meta_data
        md_devices = [self.parent._metadata, self._metadata, self._descanner._metadata, self._scanner._metadata]
        for md_dev in md_devices:
            for key in md_dev.keys():
                if key not in md:
                    md[key] = md_dev[key]
                elif key in (model.MD_HW_NAME, model.MD_HW_VERSION, model.MD_SW_VERSION):
                    # TODO for update simulator version here the ASM_service version, SAM firmware etc. is merged
                    md[key] = ", ".join([md[key], md_dev[key]])
        return md

    def _setAcqDelay(self, delay):
        """
        Setter which prevents the MPPC detector from recording before the ebeam scanner has started for the delay
        between starting the scanner and starting the recording.

        :param delay (tuple): x,y seconds
        :return (tuple): x,y seconds
        """
        # Check if detector can record images before ebeam scanner has started to scan.
        if delay >= self._scanner.scanDelay.value[0]:
            return delay
        else:
            # Change Acq Delay value so that the mppc does not start recording before the ebeam scanner has started to
            # scan.
            logging.warning("Detector cannot record images before ebeam scanner has started to scan.\n"
                            "Detector needs to start after scanner.")
            delay = self._scanner.scanDelay.value[0]
            logging.info("The adjusted acquisition delay used is %s in the eBeamScanner and the scan delay for the "
                         "MPPC is %s" % (delay, self._scanner.scanDelay.value[0]))
            return delay

    def _setFilename(self, file_name):
        """
        Check if filename complies with set allowed characters
        :param file_name (string):
        :return: file_name (string)
        """
        ASM_FILE_ILLEGAL_CHARS = r'[^a-z0-9_()-]'
        if re.search(ASM_FILE_ILLEGAL_CHARS, file_name):
            logging.warning("File_name contains invalid characters, file_name remains unchanged (only the characters "
                            "'%s' are allowed)." % ASM_FILE_ILLEGAL_CHARS[2:-1])
            return self.filename.value
        else:
            return file_name

    def _setCellTranslation(self, cellTranslation):
        """
        Setter for the cell translation, each cell has a translation (overscan_parameters) stored as an "x,y" (tuple)
        which packed with all the translations of a full row in a tuple. Which in its turn is packed in a
        tuple  with all the columns together.
        This setter checks the correct shape of the nested tuples, the type and minimum value.

        :param cellTranslation: (nested tuple)
        :return: cell translation: (nested tuple)
        """
        if len(cellTranslation) != self._shape[0]:
            raise ValueError("An incorrect shape of the cell translation parameters is provided.\n "
                             "Please change the shape of the cell translation parameters according to the shape of the "
                             "MPPC detector.\n "
                             "Cell translation parameters remain unchanged.")

        for row, cellTranslationRow in enumerate(cellTranslation):
            if len(cellTranslationRow) != self._shape[1]:
                raise ValueError("An incorrect shape of the cell translation parameters is provided.\n"
                                 "Please change the shape of the cellTranslation parameters according to the shape of "
                                 "the MPPC detector.\n "
                                 "Cell translation parameters remain unchanged.")

            for column, eff_origin in enumerate(cellTranslationRow):
                if not isinstance(eff_origin, tuple) or len(eff_origin) != 2:
                    raise ValueError("Incorrect cell translation parameters provided, wrong number/type of coordinates "
                                     "for cell (%s, %s) are provided.\n"
                                     "Please provide an 'x effective origin' and an 'y effective origin' for this cell "
                                     "image.\n "
                                     "Cell translation parameters remain unchanged." %
                                     (row, column))

                if not isinstance(eff_origin[0], int) or not isinstance(eff_origin[1], int):
                    raise ValueError(
                            "An incorrect type is used for the cell translation coordinates of cell (%s, %s).\n"
                            "Please use type integer for both 'x effective origin' and and 'y effective "
                            "origin' for this cell image.\n"
                            "Type expected is: '(%s, %s)' type received '(%s, %s)'\n"
                            "Cell translation parameters remain unchanged." %
                            (row, column, int, int, type(eff_origin[0]), type(eff_origin[1])))

                elif eff_origin[0] < 0 or eff_origin[1] < 0:
                    raise ValueError("Please use a minimum of 0 cell translation coordinates of cell (%s, %s).\n"
                                     "Cell translation parameters remain unchanged." %
                                     (row, column))
        return cellTranslation

    def _setCellDigitalGain(self, cellDigitalGain):
        """
        Setter for the digital gain of the cells, each cell has a digital gain (compensating for the differences in
        gain for the grey values in each detector cell) stored as an integer which packed with all the values for
        digital gain of a full row in a tuple. Which in its turn is packed
        in a tuple  with all the columns together.

        This setter checks the correct shape of the nested tuples, the type and minimum value.

        :param cellDigitalGain: (nested tuple)
        :return: cellDigitalGain: (nested tuple)
        """
        if len(cellDigitalGain) != self._shape[0]:
            raise ValueError("An incorrect shape of the digital gain parameters is provided. Please change the "
                             "shape of the digital gain parameters according to the shape of the MPPC detector.\n"
                             "Digital gain parameters value remain unchanged.")

        for row, cellDigitalGain_row in enumerate(cellDigitalGain):
            if len(cellDigitalGain_row) != self._shape[1]:
                raise ValueError("An incorrect shape of the digital gain parameters is provided.\n"
                                 "Please change the shape of the digital gain parameters according to the shape of the "
                                 "MPPC detector.\n "
                                 "Digital gain parameters value remain unchanged.")

            for column, DigitalGain in enumerate(cellDigitalGain_row):
                if isinstance(DigitalGain, int):
                    # Convert all input values to floats.
                    logging.warning("Input integer values for the digital gain are converted to floats.")
                    cellDigitalGain = tuple(tuple(float(cellDigitalGain[i][j]) for j in range(0, self.shape[0]))
                                            for i in range(0, self.shape[0]))
                    # Call the setter again with all int values converted to floats and return the output
                    return self._setCellDigitalGain(cellDigitalGain)

                elif not isinstance(DigitalGain, float):
                    raise ValueError("An incorrect type is used for the digital gain parameters of cell (%s, %s).\n"
                                     "Please use type fl oat for digital gain parameters for this cell image.\n"
                                     "Type expected is: '%s' type received '%s' \n"
                                     "Digital gain parameters value remain unchanged." %
                                     (row, column, float, type(DigitalGain)))
                elif DigitalGain < 0:
                    raise ValueError("Please use a minimum of 0 for digital gain parameters of cell image (%s, %s).\n"
                                     "Digital gain parameters value remain unchanged." %
                                     (row, column))

        return cellDigitalGain

    def _setCellDarkOffset(self, cellDarkOffset):
        """
        Setter for the dark offset of the cells, each cell has a dark offset (compensating for the offset in darkness
        in each detector cell) stored as an integer which packed with all the values for dark offset of a full row in
        a tuple. Which in its turn is packed in a tuple  with all the columns together. This setter checks the
        correct shape of the nested tuples, the type and minimum value.

        :param cellDarkOffset: (nested tuple)
        :return: cellDarkOffset: (nested tuple)
        """
        if len(cellDarkOffset) != self._shape[0]:
            raise ValueError("An incorrect shape of the dark offset parameters is provided.\n"
                             "Please change the shape of the dark offset parameters according to the shape of the MPPC "
                             "detector.\n "
                             "Dark offset parameters value remain unchanged.")

        for row, cellDarkOffsetRow in enumerate(cellDarkOffset):
            if len(cellDarkOffsetRow) != self._shape[1]:
                raise ValueError("An incorrect shape of the dark offset parameters is provided.\n"
                                 "Please change the shape of the dark offset parameters according to the shape of the "
                                 "MPPC detector.\n "
                                 "Dark offset parameters value remain unchanged.")

            for column, DarkOffset in enumerate(cellDarkOffsetRow):
                if not isinstance(DarkOffset, int):
                    raise ValueError("An incorrect type is used for the dark offset parameter of cell (%s, "
                                     "%s). \n"
                                     "Please use type integer for dark offset for this cell image.\n"
                                     "Type expected is: '%s' type received '%s' \n"
                                     "Dark offset parameters value remain unchanged." %
                                     (row, column, float, type(DarkOffset)))

                elif DarkOffset < 0:
                    raise ValueError("Please use a minimum of 0 for dark offset parameters of cell image (%s, %s).\n"
                                     "Dark offset parameters value remain unchanged." %
                                     (row, column))

        return cellDarkOffset


class ASMDataFlow(model.DataFlow):
    """
    Represents the acquisition on the ASM
    """

    def __init__(self, mppc):
        super(ASMDataFlow, self).__init__(self)

        # Make MPPC object an private attribute (which is used to call the start, next, stop and get methods)
        self._mppc = mppc

    def start_generate(self):
        """
        Start the dataflow using the provided function. The appropriate settings are retrieved via the VA's of the
        each component
        """
        self._mppc.start_acquisition()

    def next(self, field_num):
        """
        Acquire the next field image using the provided function.
        :param field_num (tuple): tuple with x,y coordinates in integers of the field number.
        """
        self._mppc.get_next_field(field_num)

    def stop_generate(self):
        """
        Stop the dataflow using the provided function.
        """
        self._mppc.stop_acquisition()

    def get(self, *args, **kwargs):
        """
        Acquire a single field, can only be called if no other acquisition is active.
        :return: (DataArray)
        """
        if self._count_listeners() < 1:
            # Acquire and return received image
            image = self._mppc.acquire_single_field(*args, **kwargs)
            return image

        else:
            logging.error("There is already an acquisition on going with %s listeners subscribed, first cancel/stop "
                          "current running acquisition to acquire a single field-image" % self._count_listeners())
            raise Exception("There is already an acquisition on going with %s listeners subscribed, first cancel/stop "
                            "current running acquisition to acquire a single field-image" % self._count_listeners())


class AsmApiException(Exception):
    """
    Exception for raising errors while calling the ASM API.
    """

    def __init__(self, url, response, expected_status, ASM):
        """
        Initializes exception object defining the error message to be displayed to the user as a response.
        And performs basic checks on ASM items to see of those are not the cause of the error. (e.g. monitor/item
        sam_connection_operational, ext_store_connection_operational, offload_queue_fill_level, install_in_progress,
        last_install_success)

        :param url: URL of the call tried which was tried to make
        :param response: full/raw response from the ASM API
        :param expected_status: the expected status code
        :param ASM (AcquisitionServer object) : AcquisitionServer object used for changed the state of the system and
        checking system parameters.
        """
        self.url = url
        self.status_code = response.status_code
        self.reason = response.reason
        self.expected_status = expected_status

        # TODO Currently the system/"monitor" checks in "system_checks(ASM) are not supported in the simulator,
        #  they are only defined in the  API. Therefore this method is commented to prevent creating errors.
        # self.system_checks(ASM)  # Perform general checks on the ASM system to find the cause of the error.

        try:
            self.content_translated = json.loads(response.content)
            self._errorMessageResponse(self.content_translated['status_code'],
                                       self.content_translated['message'])
        except:
            if hasattr(response, "text"):
                self._errorMessageResponse(self.status_code, response.text)
            elif hasattr(response, "content"):
                self._errorMessageResponse(self.status_code, response.content)
            else:
                self._emptyResponse()
        finally:
            # Also log the error so it is easier to find it back when the error was received in the log
            logging.error(self._error)

    def __str__(self):
        # For displaying the error
        return self._error

    def _errorMessageResponse(self, error_code, error_message):
        """
        Defines the error message if and response holding information is received from the ASM.

        :param error_code (int): received status_code
        :param error_message (str): received error message (translated from json, via a dict to a str)
        """
        self._error = ("\n"
                       "Call to %s received unexpected answer.\n"
                       "Received status code '%s' because of the reason '%s', but expected status code was'%s'\n"
                       "Error status code '%s' with the message: '%s'\n" %
                       (self.url,
                        self.status_code, self.reason, self.expected_status,
                        error_code, error_message))

    def _emptyResponse(self):
        """
        Defines the error message if the response received from the ASM does not hold the proper error
        information.
        """
        self._error = ("\n"
                       "Call to %s received unexpected answer.\n"
                       "Got status code '%s' because of the reason '%s', but expected '%s'\n" %
                       (self.url,
                        self.status_code, self.reason, self.expected_status))

    def system_checks(self, ASM):
        """
        Performs default checks on the system, to help inform the user if any problem in the system might be a cause of
        the error. If a negative outcome of these logged and the HW state of the system is changed to an error.

        :param ASM (AcquisitionServer object) : AcquisitionServer object used for changed the state of the system and
        checking system parameters.
        """
        try:
            response = ASM.ASM_API_Get_Call("sam_connection_operational", 200)
            if not response:
                ASM.state._set_value(HwError("Sam connection not operational."), force_write=True)
                logging.error("Sam connection not operational.")
        except:
            logging.error("Checking if the sam connection is operational failed.")

        try:
            response = ASM.ASM_API_Get_Call("ext_store_connection_operational", 200)
            if not response:
                ASM.state._set_value(HwError("External storage connection not operational."), force_write=True)
                logging.error("External storage connection not operational.\n"
                              "When the connection with the external storage is lost, scanning is "
                              "still possible. There is a large offload queue that can hold field images. "
                              "Until there is space left in that queues, field scanning can continue.")
        except:
            logging.error("Checking the external storage connection failed.")

        try:
            item_name = "install_in_progress"
            response = ASM.ASM_API_Get_Call(item_name, 200)
            if response:
                ASM.state._set_value(HwError("Installation in progress."), force_write=True)
                logging.error("An installation is in progress.")
        except:
            logging.error("Checking if an installation is in progress failed.")

        try:
            item_name = "last_install_success"
            response = ASM.ASM_API_Get_Call(item_name, 200)
            if not response:
                ASM.state._set_value(HwError("Last installation was unsuccessful."), force_write=True)
                logging.error(response)
        except:
            logging.error("Checking if last installation was successful failed.")

        try:
            item_name = "offload_queue_fill_level"  # defined item_name for logging message in except.
            max_queue_fill = 99  # queue filling level can already be problematic at values lower than 99%, test this.
            response = ASM.ASM_API_Get_Call(item_name, 200)
            if response >= max_queue_fill:
                ASM.state._set_value(HwError("The offload queue is full, filling rate is: %s percent." % response),
                                     force_write=True)
                logging.error(" Fill rate of the queue in percent: 0 .. 100. When the connection with the external "
                              "storage is lost, images will be stored in the offload queue. When the queue fill level "
                              "is nearly 100 percent, field scanning is not possible anymore.\n"
                              "The filling rate of the que is now at %s percent." % response)
        except:
            logging.error("Checking monitor status of %s failed" % item_name)


class TerminationRequested(Exception):
    """
    Acquisition termination requested closing the acquisition thread in the _acquire method.
    """
    pass

