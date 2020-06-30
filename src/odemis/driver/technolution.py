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
from urllib.parse import urlparse, urlunparse

from odemis import model
from requests import Session
from scipy import signal

from openapi_server.models.calibration_loop_parameters import CalibrationLoopParameters
# TODO K.K. will change package/folder name for next simulator
from src.openapi_server.models.cell_parameters import CellParameters
from src.openapi_server.models.field_meta_data import FieldMetaData
from src.openapi_server.models.mega_field_meta_data import MegaFieldMetaData

DATA_CONTENT_TO_ASM = {"empty": None, "thumbnail": True, "full": False}
#TODO K.K. was the plan to use this or not?
VERSIONS_SUPPORTED = {
    "asm": {"service_version": ["34e2070", "older versions"], },
    "sam": {"firmware_version": [],
            "rootfs_version"  : [],
            "service_version" : []}
}

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
        :param host (str): URL of the ASM host
        :param children (dict): dictionary containing HW components and there respective configuration
        :param kwargs:
        """

        super(AcquisitionServer, self).__init__(name, role, **kwargs)

        self._server_url = host
        # Use session object avoids creating a new connection for each message sent
        # (note: Session() auto-reconnects if the connection is broken for a new call)
        self._session = Session()

        # Stop any acquisition if already one was in progress
        self.ASM_API_Post_Call("/scan/finish_mega_field", 204)

        # NOTE: Do not write real username/password here since this is published on github in plaintext!
        # example = ftp://username:password@example.com/Pictures
        self.externalStorageURL = model.VigilantAttribute(urlparse('ftp://%s:%s@%s.com/%s' %
                                                                   (externalStorage["username"],
                                                                    externalStorage["password"],
                                                                    externalStorage["host"],
                                                                    externalStorage["directory"])),
                                                          setter=self._setURL)
        # VA's for calibration loop
        self.calibrationMode = model.BooleanVA(False, setter=self._setCalibrationMode)
        # Frequency of the calibration signal
        self.calibrationFrequency = model.IntContinuous(125, range=(125, 5000), unit="Hz")

        self.ASM_API_Post_Call("/config/set_external_storage?host=%s&user=%s&password=%s" %
                               (self.externalStorageURL.value.hostname,
                                self.externalStorageURL.value.username,
                                self.externalStorageURL.value.password), 204)
        self.ASM_API_Post_Call("/config/set_system_sw_name?software=%s" % name, 204)

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
        self._stopCalibration()
        # terminate children
        for child in self.children.value:
            child.terminate()
        self._session.close()

    def ASM_API_Get_Call(self, url, expected_status, data=None, raw_response=False, timeout=600, **kwargs):
        """
        Call to the ASM API to get data from the ASM API

        :param url (str): url of the command, server part is defined in object variable self._server_url
        :param expected_status (int): expected feedback of server for a positive call
        :param data: data (request body) added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response (bool): specified the format of the structure returned
        :param timeout (int): [s] if within this period no bytes are received an timeout exception is raised
        :return: content dictionary(getting), or entire response (raw_response=True)
        """
        logging.debug("Executing: %s" % url)
        resp = self._session.get(self._server_url + url, json=data, timeout=timeout, **kwargs)

        if resp.status_code != expected_status:
            raise AsmApiException(url, resp, expected_status, self.ASM_API_Get_Call)

        logging.debug("Call to %s went fine, no problems occured\n" % url)
        if raw_response:
            return resp
        else:
            return json.loads(resp.content)

    def ASM_API_Post_Call(self, url, expected_status, data=None, raw_response=False, timeout=600, **kwargs):
        """
        Call to the ASM API to post data to the ASM API

        :param url (str): url of the command, server part is defined in object variable self._server_url
        :param expected_status (int): expected feedback of server for a positive call
        :param data: data (request body) added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response (bool): specified the format of the structure returned
        :param timeout (int): [s] if within this period no bytes are received an timeout exception is raised
        :return: status_code(int) or entire response (raw_response=True)
        """
        logging.debug("Executing: %s" % url)
        resp = self._session.post(self._server_url + url, json=data, timeout=timeout, **kwargs)

        if resp.status_code != expected_status:
            raise AsmApiException(url, resp, expected_status + 1, self.ASM_API_Get_Call)

        logging.debug("Call to %s went fine, no problems occurred\n" % url)
        if raw_response:
            return resp
        else:
            return resp.status_code

    def _startCalibration(self, *args):
        self.ASM_API_Post_Call("/scan/stop_calibration_loop", 204)
        descanner = self._mirror_descanner
        scanner = self._ebeam_scanner

        # Retrieving and subscribing the descanner VA's
        descan_rotation = descanner.rotation.value
        descanner.rotation.subscribe(self._startCalibration)
        descan_offset = descanner.scanOffset.value
        descanner.scanOffset.subscribe(self._startCalibration)
        descanner.scanGain.subscribe(self._startCalibration)

        # Retrieving and subscribing the scanner VA's
        dwell_time = scanner.getTicksDwellTime
        scanner.dwellTime.subscribe(self._startCalibration)
        scan_rotation = scanner.rotation.value
        scanner.rotation.subscribe(self._startCalibration)
        scan_delay = scanner.scanDelay.value
        scanner.scanDelay.subscribe(self._startCalibration)
        scan_offset = scanner.scanOffset.value
        scanner.scanOffset.subscribe(self._startCalibration)
        scanner.scanGain.subscribe(self._startCalibration)

        # Creation of setpoints
        calibration_frequency = self.calibrationFrequency.value  # Frequency of the calibration signal in Hz
        self.calibrationFrequency.subscribe(self._startCalibration)

        time_points = numpy.arange(0, 100 / calibration_frequency, descanner.clockPeriod.value)
        x_descan_setpoints = descanner.scanGain.value[0] * numpy.sin(2 * numpy.pi * calibration_frequency * time_points)
        y_descan_setpoints = descanner.scanGain.value[1] * signal.sawtooth(2 * numpy.pi * calibration_frequency *
                                                                           time_points)
        x_scan_setpoints = scanner.scanGain.value[0] * numpy.sin(2 * numpy.pi * calibration_frequency * time_points)
        y_scan_setpoints = scanner.scanGain.value[1] * signal.sawtooth(2 * numpy.pi * calibration_frequency *
                                                                       time_points)

        callibration_data = CalibrationLoopParameters(descan_rotation,
                                                      descan_offset[0],
                                                      x_descan_setpoints.astype(int).tolist(),
                                                      descan_offset[1],
                                                      y_descan_setpoints.astype(int).tolist(),
                                                      dwell_time,
                                                      scan_rotation,
                                                      scan_delay[0],
                                                      scan_offset[0],
                                                      x_scan_setpoints.astype(int).tolist(),
                                                      scan_offset[1],
                                                      y_scan_setpoints.astype(int).tolist())

        self.ASM_API_Post_Call("/scan/start_calibration_loop", 204, data=callibration_data.to_dict())

    def _stopCalibration(self):
        """
        Unsubscribe _startCalibration from all the VA's so the calibration loop is not restarted when a VA is changed.
        :return:
        """
        self.calibrationFrequency.unsubscribe(self._startCalibration)

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

        self.ASM_API_Post_Call("/scan/stop_calibration_loop", 204)

    def _setCalibrationMode(self, mode):
        if mode:
            # TODO K.K. setup standard VA values for calibration mode
            self._mirror_descanner.scanGain.value = (1.0, 0.0)
            self._ebeam_scanner.scanGain.value = (1.0, 0.0)
            self._startCalibration()  # Start calibration loop and subscribe to VA's to reset calibration loop
            return True

        else:
            self._stopCalibration()  # Stop calibration loop and unsubscribe from all the VA's
            # TODO K.K. return to standard VA values for acquisition mode
            self._mirror_descanner.scanGain.value = (1.0, 1.0)
            self._ebeam_scanner.scanGain.value = (1.0, 1.0)
            return False

    def _setURL(self, url_parser):
        """
        Setter which checks for correctness of FTP url_parser and otherwise returns old value.

        :param url_parser: e.g. ftp://username:password@example.com
        :return: correct ftp url_parser
        """
        ASM_GENERAL_ALLOWED_CHARS = r'[^A-Za-z0-9/_()-:@]'
        ASM_USER_ALLOWED_CHARS = r'[^A-Za-z0-9]'
        ASM_PASSWORD_ALLOWED_CHARS = r'[^A-Za-z0-9]'
        ASM_HOST_ALLOWED_CHARS = r'[^A-Za-z0-9.]'
        ASM_PATH_ALLOWED_CHARS = r'[^A-Za-z0-9/_()-]'

        def checkCharacters(input, allowed_characters):
            """
            Check if input complies with allowed characters
            :param input (sting): input string
            :param allowed_characters: allowed_characters for different parts of input string
            :return (boolean) True if passes test on allowed_characters
            """
            search = re.compile(allowed_characters).search
            if not bool(search(input)):
                return True
            else:
                return False

        # Perform general check on valid characters (parses works incorrectly for some invalid characters
        if not checkCharacters(urlunparse(url_parser), ASM_GENERAL_ALLOWED_CHARS):
            logging.warning("Invalid character in ftp url is provided, allowed characters are %s in the form:: "
                            "'ftp://username:password@host_example.com/path/to/Pictures'\n"
                            "(Only use the @ to separate the password and the host." % ASM_GENERAL_ALLOWED_CHARS[2:-1])
            return self.externalStorageURL.value

        # Perform detailed checks on input
        if url_parser.scheme != 'ftp' \
                or not url_parser.scheme or not url_parser.username or not url_parser.password \
                or not url_parser.hostname or not url_parser.path:
            # Check both the scheme as well if all sub-elements are non-empty
            # Note that if an extra @ is used (e.g. in the password) the parser works incorrectly and sub-elements
            # are empty after splitting the url input
            logging.warning("Incorrect ftp url is provided, please use form: "
                            "'ftp://username:password@host_example.com/path/to/Pictures'\n"
                            "(Only use the @ to separate the password and the host.")
            return self.externalStorageURL.value

        elif not checkCharacters(url_parser.username, ASM_USER_ALLOWED_CHARS):
            logging.warning(
                    "Username contains invalid characters, username remains unchanged "
                    "(only the characters '%s' are allowed)" % ASM_USER_ALLOWED_CHARS[2:-1])
            return self.externalStorageURL.value

        elif not checkCharacters(url_parser.password, ASM_PASSWORD_ALLOWED_CHARS):
            logging.warning(
                    "Password contains invalid characters, password remains unchanged "
                    "(only the characters '%s' are allowed)" % ASM_PASSWORD_ALLOWED_CHARS[2:-1])
            return self.externalStorageURL.value

        elif not checkCharacters(url_parser.hostname, ASM_HOST_ALLOWED_CHARS):
            logging.warning(
                    "Host contains invalid characters, host remains unchanged "
                    "(only the characters '%s' are allowed)" % ASM_HOST_ALLOWED_CHARS[2:-1])
            return self.externalStorageURL.value

        elif not checkCharacters(url_parser.path, ASM_PATH_ALLOWED_CHARS):
            logging.warning("Path on ftp server contains invalid characters, path remains unchanged "
                            "(only the characters '%s' are allowed)" % ASM_PATH_ALLOWED_CHARS[2:-1])
        else:
            return url_parser


class EBeamScanner(model.Emitter):
    """
    Represents the e-beam scanner of a single field image.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initialize the e-beam scanner of a single field image..

        :param name:
        :param role:
        :param parent:
        :param kwargs:
        """
        super(EBeamScanner, self).__init__(name, role, parent=parent, **kwargs)

        clockFrequencyData = self.parent.ASM_API_Get_Call("/scan/clock_frequency", 200)
        # Check if clockFrequencyData holds the proper keysa
        if 'frequency' not in clockFrequencyData:
            raise IOError("Could not obtain clock frequency, received data does not hold the proper key")
        self.clockPeriod = model.FloatVA(1 / clockFrequencyData['frequency'], unit='s', readonly=True)
        self._shape = (6400, 6400)
        # The resolution min/maximum are derived from the effective cell size restriction defined in the API
        self.resolution = model.ResolutionVA((6400, 6400), ((10, 10), (1000 * 8, 1000 * 8)))
        self.dwellTime = model.FloatContinuous(self.clockPeriod.value, (max(self.clockPeriod.value, 4e-7), 1e-4),
                                               unit='s')
        self.pixelSize = model.TupleContinuous((4e-9, 4e-9), range=((1e-9, 1e-9), (1e-3, 1e-3)), unit='m',
                                               setter=self._setPixelSize)
        self.rotation = model.FloatContinuous(0, range=(0, 2 * numpy.pi), unit='rad')

        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')
        self.scanGain = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')

        # TODO K.K. this unit's max cannot be in seconds! + the matching MPPC unit (checked in setter neither)
        # TODO K.K. check if relation between scanDelay and acq_delay is still valid with setpoints
        self.scanDelay = model.TupleContinuous((0, 0), range=((0, 0), (100000, 10)), unit='s',
                                               setter=self._setScanDelay)

        self._metadata[model.MD_PIXEL_SIZE] = self.pixelSize.value
        self._metadata[model.MD_DWELL_TIME] = self.dwellTime.value

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
            return (pixelSize[0], pixelSize[0])

    def _setScanDelay(self, scanDelay):
        """
        Setter which checks if detector can record images before ebeam scanner has started to scan.

        :param pixelSize (tuple):
        :return (tuple):
        """
        # Check if detector can record images before ebeam scanner has started to scan.
        if not (hasattr(self.parent, "_mppc")) or self.parent._mppc.acqDelay.value >= scanDelay[0]:
            return scanDelay
        else:
            # Change values so that 'self.parent._mppc. acqDelay.value - self.scanDelay.value[0]' has a positive result
            logging.warning("Detector cannot record images before ebeam scanner has started to scan.\n"
                            "Detector needs to start after scanner.")
            logging.info("The entered acquisition delay is %s in the eBeamScanner and the scan delay in the MPPC is "
                         "%s" % (scanDelay[0], self.parent._mppc.acqDelay.value))
            return self.scanDelay.value

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


class MirrorDescanner(model.Emitter):
    """
    Represents the Mirror de scanner of a single field image which counter scans the scanner movement to ensure that
    the collected signal always hits the center of each mppc cell on the detector.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initializes Mirror de scanner of a single field image which counter scans the scanner movement to ensure
        that the collected signal always hits the center of each mppc cell on the detector.

        :param name:
        :param role:
        :param parent:
        :param kwargs:
        """
        super(MirrorDescanner, self).__init__(name, role, parent=parent, **kwargs)

        self.rotation = model.FloatContinuous(0, range=(0, 2 * numpy.pi), unit='rad')
        self.scanOffset = model.TupleContinuous((4000, 4000), range=((-32768, -32768), (32767, 32767)), unit='V')
        self.scanGain = model.TupleContinuous((10.0, 10.0), range=((-1000.0, -1000.0), (1000.0, 1000.0)), unit='V')

        clockFrequencyData = self.parent.ASM_API_Get_Call("/scan/descan_control_frequency", 200)
        # Check if clockFrequencyData holds the proper key
        if 'frequency' not in clockFrequencyData:
            raise IOError("Could not obtain clock frequency, received data does not hold the proper key")
        self.clockFrequency = clockFrequencyData['frequency']
        self.clockPeriod = model.FloatVA(1 / clockFrequencyData['frequency'], unit='s', readonly=True)

        # TODO check if physicalFlybackTime is constant and what time it should be for EA --> Wilco & Andries
        # Physical time for the mirror descanner to perform a flyback, assumed contstant [s].
        self.physicalFlybackTime = 250e-6

    def _getXAcqSetpoints(self):
        scan_period = self.clockPeriod.value
        scan_gain = self.scanGain.value
        dwelltime = self.parent._ebeam_scanner.dwellTime.value  # s
        scan_delay = self.parent._ebeam_scanner.scanDelay.value  # s
        cell_size = self.parent._mppc.cellCompleteResolution.value  # pixels
        acq_delay = self.parent._mppc.acqDelay.value - self.parent._ebeam_scanner.scanDelay.value[0]


        # Function to find off beatness of the setpoint lines
        # "(scan_period - input % scan_period) % scan_period" because when input is a multiple of scan_period
        # the outcome should be 0, other wise the outcome could be the time left in that scan_period.
        off_beat = lambda input: (scan_period - input % scan_period) % scan_period

        # all units in seconds
        #TODO K.K. if testcases in place change arange to zeros where multiplied with zero
        scan_delay_points = 0 * numpy.arange(0, scan_delay[0], scan_period)
        acq_delay_points = 0 * numpy.arange(off_beat(scan_delay[0]), acq_delay, scan_period)
        rise_points = numpy.array(list(map(lambda t: (scan_gain[0] / dwelltime) * t,
                                           numpy.arange(off_beat(scan_delay[0] + acq_delay),
                                                        dwelltime * cell_size[0], scan_period))))

        flyback_points = 0 * numpy.arange(0, self.physicalFlybackTime, scan_period)
        setpoints_row = numpy.tile(numpy.concatenate((acq_delay_points, rise_points, flyback_points)), cell_size[1])
        base = self.scanOffset.value[0] - 0.5 * cell_size[0] * scan_gain[0]
        setpoints = base + numpy.concatenate((scan_delay_points, setpoints_row))

        if setpoints.min() < -32768 or setpoints.max() > 32767:
            raise ValueError("Setpoint values are to big/small to be handled by the ASM API")
        return setpoints.astype(int).tolist()

    def _getYAcqSetpoints(self):
        scan_period = self.clockPeriod.value
        scan_gain = self.scanGain.value
        dwelltime = self.parent._ebeam_scanner.dwellTime.value  # s
        scan_delay = self.parent._ebeam_scanner.scanDelay.value  # s
        cell_size = self.parent._mppc.cellCompleteResolution.value  # pixels
        acq_delay = self.parent._mppc.acqDelay.value - self.parent._ebeam_scanner.scanDelay.value[0]

        # Function to find off beatness of the setpoint lines
        # "(scan_period - input % scan_period) % scan_period" because when input is a multiple of scan_period
        # the outcome should be 0, other wise the outcome could be the time left in that scan_period.
        off_beat = lambda input: (scan_period - input % scan_period) % scan_period

        # all units in seconds
        #TODO K.K. if testcases in place change arange to zeros
        scan_delay_points = 0 * numpy.arange(0, scan_delay[1], scan_period)
        acq_delay_points = 0 * numpy.arange(off_beat(scan_delay[1]), acq_delay, scan_period)

        rise_points = 0 * numpy.arange(off_beat(scan_delay[1] + acq_delay),
                                                            dwelltime * cell_size[0], scan_period)

        #TODO K.K. remove part compairing and checking
        rise_points1 = 0 * numpy.array(list(map(lambda t: (scan_gain[1] / dwelltime) * t,
                                               numpy.arange(off_beat(scan_delay[1] + acq_delay),
                                                            dwelltime * cell_size[0], scan_period))))

        if not numpy.array_equal(rise_points1, rise_points):
            raise ValueError

        flyback_points = 0 * numpy.arange(0, self.physicalFlybackTime, scan_period)
        setpoints_row = numpy.concatenate((acq_delay_points, rise_points, flyback_points))
        setpoints = numpy.array([scan_gain[1] * i + setpoints_row for i in range(0, cell_size[1])]).reshape(-1)
        base = self.scanOffset.value[1] - 0.5 * cell_size[0] * scan_gain[1]
        setpoints = base + numpy.concatenate((scan_delay_points, setpoints))

        if setpoints.min() < -32768 or setpoints.max() > 32767:
            raise ValueError("Setpoint values are to big/small to be handeled by the ASM API")
        return setpoints.astype(int).tolist()

    def getAcqSetpoints(self):
        """
        :return: X_descan_setpoints [ints], Y_descan_setpoints[ints]
        """
        X_descan_setpoints = self._getXAcqSetpoints()
        Y_descan_setpoints = self._getYAcqSetpoints()

        if len(X_descan_setpoints) != len(Y_descan_setpoints):
            raise ValueError("Non correctly created setpoints")

        return X_descan_setpoints, Y_descan_setpoints

    def getAdjFlybackTime(self):
        """
        :return: Descanner flyback time adjusted for giving full descan periods to scanner
        """
        scan_to_acq_delay = self.parent._mppc.acqDelay.value - self.parent._ebeam_scanner.scanDelay.value[0]
        adjustment = (scan_to_acq_delay + self.parent._ebeam_scanner.dwellTime.value *
                      self.parent._mppc.cellCompleteResolution.value[0]) // self.clockPeriod.value
        adjusted_flyback_time = self.physicalFlybackTime + adjustment
        return adjusted_flyback_time


class MPPC(model.Detector):
    """
    Represents the camera (mppc sensor) for acquiring the image data.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initializes the camera (mppc sensor) for acquiring the image data.

        :param name:
        :param role:
        :param parent:
        :param kwargs:
        """
        super(MPPC, self).__init__(name, role, parent=parent, **kwargs)

        self._shape = (8, 8)
        self.filename = model.StringVA(time.strftime("default--%Y-%m-%d-%H-%M-%S"), setter=self._setFilename)
        self.dataContent = model.StringEnumerated('empty', DATA_CONTENT_TO_ASM.keys())
        self.acqDelay = model.FloatContinuous(0.001, range=(0, 100000), unit='s', setter=self._setAcqDelay)

        # Cell acquisition parameters
        self.cellTranslation = model.ListVA([[[50, 50]] * self._shape[0]] * self._shape[1],
                                            setter=self._setCellTranslation)
        self.cellDarkOffset = model.ListVA([[0] * self._shape[0]] * self._shape[1], setter=self._setcellDarkOffset)
        self.cellDigitalGain = model.ListVA([[1.2] * self._shape[0]] * self._shape[1], setter=self._setcellDigitalGain)
        self.cellCompleteResolution = model.ResolutionVA((800, 800), ((10, 10), (1000, 1000)))

        # TODO K.K. pass right metadata from new simulator

        # Setup hw and sw version
        # TODO make call set_system_sw_name to new simulator (if implemented)
        self._swVersion = self._swVersion + ", " + "PUT NEW SIMULATOR DATA HERE"
        self._hwVersion = self._hwVersion + ", " + "PUT NEW SIMULATOR DATA HERE"

        # Gather metadata from all related HW components and own _meta_data
        self.md_devices = [self._metadata, self.parent._mirror_descanner._metadata,
                           self.parent._ebeam_scanner._metadata]
        self._metadata[model.MD_HW_NAME] = "MPPC" + "/" + name
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_HW_VERSION] = self._hwVersion

        # Initialize acquisition processes
        self.acq_queue = queue.Queue()  # acquisition queue with commands of actions that need to be executed.
        self._acq_thread = threading.Thread(target=self._acquire, name="acquisition thread")
        self._acq_thread.deamon = False
        self._acq_thread.start()

        self.data = ASMDataFlow(self.start_acquisition, self.get_next_field,
                                self.stop_acquisition, self.acquire_single_field)

    def terminate(self):
        """
        Terminate acquisition thread and empty the queue
        """
        super(MPPC, self).terminate()

        # Clear the queue
        while True:
            try:
                self.acq_queue.get(block=False)
            except queue.Empty:
                break

        self.acq_queue.put(("terminate", None))

    def _assemble_megafield_metadata(self):
        """
        Gather all the megafield metadata from the VA's and convert into a MegaFieldMetaData Model using the ASM API

        :return: MegaFieldMetaData Model of the ASM API
        """
        cellTranslation = sum(self.cellTranslation.value, [])
        celldarkOffset = sum(self.cellDarkOffset.value, [])
        celldigitalGain = sum(self.cellDigitalGain.value, [])
        eff_cell_size = (int(self.parent._ebeam_scanner.resolution.value[0] / self._shape[0]),
                         int(self.parent._ebeam_scanner.resolution.value[1] / self._shape[1]))

        scan_to_acq_delay = int((self.acqDelay.value - self.parent._ebeam_scanner.scanDelay.value[0]) /
                            self.parent._ebeam_scanner.clockPeriod.value)

        X_descan_setpoints, Y_descan_setpoints = self.parent._mirror_descanner.getAcqSetpoints()

        megafield_metadata = \
            MegaFieldMetaData(
                    mega_field_id=self.filename.value,
                    storage_directory=self.parent.externalStorageURL.value.path,
                    custom_data="No_custom_data",
                    stage_position_x=0.0,
                    stage_position_y=0.0,
                    # Convert pixels size from meters to nanometers
                    pixel_size=int(self.parent._ebeam_scanner.pixelSize.value[0] * 1e9),
                    dwell_time=self.parent._ebeam_scanner.getTicksDwellTime(),
                    # TODO K.K. scan to acq delay is weirdly defined an barely used, what is the proper way to declare
                    # this?
                    x_scan_to_acq_delay=scan_to_acq_delay,
                    x_scan_delay=self.parent._ebeam_scanner.scanDelay.value[0],
                    x_cell_size=self.cellCompleteResolution.value[0],
                    x_eff_cell_size=eff_cell_size[0],
                    y_cell_size=self.cellCompleteResolution.value[1],
                    y_eff_cell_size=eff_cell_size[1],
                    y_prescan_lines=self.parent._ebeam_scanner.scanDelay.value[1],
                    x_scan_gain=self.parent._ebeam_scanner.scanGain.value[0],
                    y_scan_gain=self.parent._ebeam_scanner.scanGain.value[1],
                    x_scan_offset=self.parent._ebeam_scanner.scanOffset.value[0],
                    y_scan_offset=self.parent._ebeam_scanner.scanOffset.value[1],
                    # API gives error for values < 0 but YAML does not specify so
                    x_descan_setpoints=X_descan_setpoints,
                    y_descan_setpoints=Y_descan_setpoints,
                    x_descan_offset=self.parent._mirror_descanner.scanOffset.value[0],
                    y_descan_offset=self.parent._mirror_descanner.scanOffset.value[1],
                    scan_rotation=self.parent._ebeam_scanner.rotation.value,
                    descan_rotation=self.parent._mirror_descanner.rotation.value,
                    cell_parameters=[CellParameters(translation[0], translation[1], darkOffset, digitalGain)
                                     for translation, darkOffset, digitalGain in
                                     zip(cellTranslation, celldarkOffset, celldigitalGain)],
            )

        return megafield_metadata

    def _acquire(self):
        """
        Acquisition thread takes input from the self.acq_queue which holds a command ('start', 'next', 'stop',
        'terminate') and extra arguments (MegaFieldMetaData Model or FieldMetaData Model and the notifier function to
        which any return will be redirected)
        """

        try:
            acquisition_in_progress = None  # To prevent that acquisitions mix up, or stop the acquisition twice.

            while True:
                # Wait until a message is available
                command, *args = self.acq_queue.get(block=True)

                if command == "start":
                    if acquisition_in_progress:
                        logging.warning("ASM acquisition was already at status '%s'" % command)
                        continue

                    acquisition_in_progress = True
                    megafield_metadata = args[0]
                    self.parent.ASM_API_Post_Call("/scan/start_mega_field", 204, megafield_metadata.to_dict())

                elif command == "next":
                    if not acquisition_in_progress:
                        logging.warning("Start ASM acquisition before taking field images")
                        continue

                    field_data = args[0]  # Field metadata for the specific position of the field to scan
                    dataContent = args[1]  # Specifies the type of image to return (empty, thumbnail or full)
                    notifier_func = args[2]  # Return function (usually, Dataflow.notify or acquire_single_filed queue)

                    self.parent.ASM_API_Post_Call("/scan/scan_field", 204, field_data.to_dict())

                    # TODO add metadata from queue/ASM info mergaMetadata function so that metadata is correct.
                    if DATA_CONTENT_TO_ASM[dataContent] == None:
                        da = model.DataArray(numpy.array([[0]], dtype=numpy.uint8), metadata=self._mergeMetadata())
                    else:
                        # TODO remove wait if the function "waitOnFIELDimage" exists
                        time.sleep(0.5)
                        resp = self.parent.ASM_API_Get_Call(
                                "/scan/field?x=%d&y=%d&thumbnail=%s" %
                                (field_data.position_x, field_data.position_y,
                                 str(DATA_CONTENT_TO_ASM[dataContent]).lower()),
                                200, raw_response=True, stream=True)
                        resp.raw.decode_content = True  # handle spurious Content-Encoding
                        img = Image.open(BytesIO(base64.b64decode(resp.raw.data)))

                        da = model.DataArray(img, metadata=self._mergeMetadata())

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
        Put a the command 'start' mega field scan on the queue with the appropriate MegaFieldMeta Model of the mega
        field image to be scannend. All subsequent calls to scan_field will use a part of this meta data to store the image
        data until the stop command is executed.
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
            self._acq_thread.deamon = False
            self._acq_thread.start()

        megafield_metadata = self._assemble_megafield_metadata()
        self.acq_queue.put(("start", megafield_metadata))

    def get_next_field(self, field_num):
        '''
        Put a the command 'next' field image scan on the queue with the appropriate field meta data model of the field
        image to be scannend. Can only be executed if it proceeded by a 'start' mega field scan command on the queue.
        As notifier function the dataflow.notify is given which means the returned image will be redirected to this
        function.

        :param field_num: x,y
        '''
        field_data = FieldMetaData(*self.convert_field_num2pixels(field_num))
        self.acq_queue.put(("next", field_data, self.dataContent.value, self.data.notify))

    def stop_acquisition(self):
        """
        Puts a 'stop' field image scan on the queue, after this call, no fields can be scanned anymore. A new mega
        field can be started. The call triggers the post prosessing process to generate and offload additional zoom
        levels
        """
        self.acq_queue.put(("stop",))

    def acquire_single_field(self, dataContent="thumbnail", field_num=(0, 0)):
        """
        Puts a the series 'start','next','stop' commands on the queue with the appropriate metadata models and
        scans a single field image. By providing as notifier function a return_queue the image can be returned. The
        use of the queue allows the use of the timeout functionality

        :param field_num:
        :return: DA of the single field image
        """
        if dataContent not in DATA_CONTENT_TO_ASM:
            logging.warning("Incorrect dataContent provided for acquiring a single image, thumbnail is used instead")
            dataContent = "thumbnail"

        return_queue = queue.Queue()  # queue which allows to return images and be blocked when waiting on images
        mega_field_data = self._assemble_megafield_metadata()

        self.acq_queue.put(("start", mega_field_data))
        field_data = FieldMetaData(*self.convert_field_num2pixels(field_num))

        self.acq_queue.put(("next", field_data, dataContent, return_queue.put))
        self.acq_queue.put(("stop",))

        return return_queue.get(timeout=600)

    def convert_field_num2pixels(self, field_num):
        return (field_num[0] * self.parent._ebeam_scanner.resolution.value[0],
                field_num[1] * self.parent._ebeam_scanner.resolution.value[1])

    def _mergeMetadata(self):
        """
        Create dict containing all metadata from siblings and own metadata
        """
        md = {}
        self._metadata[model.MD_ACQ_DATE] = time.time()  # Time since Epoch

        for md_dev in self.md_devices:
            for key in md_dev.keys():
                if key not in md:
                    md[key] = md_dev[key]
                elif key in (model.MD_HW_NAME, model.MD_HW_VERSION, model.MD_SW_VERSION):
                    # TODO update to add metadata call to sam_firmware_version, sam_service_version,
                    #  sam_rootfs_version,  asm_service_version
                    md[key] = ", ".join([md[key], md_dev[key]])
        return md

    def _setAcqDelay(self, delay):
        """
        Setter which checks if detector can record images before ebeam scanner has started to scan.

        :param delay (tuple):
        :return (tuple):
        """
        # Check if detector can record images before ebeam scanner has started to scan.
        if delay >= self.parent._ebeam_scanner.scanDelay.value[0]:
            return delay
        else:
            # Change values so that 'self.acqDelay.value - self.parent._ebeam_scanner.scanDelay.value[0]' has a positive result
            logging.warning("Detector cannot record images before ebeam scanner has started to scan.\n"
                            "Detector needs to start after scanner.")
            logging.info("The entered acquisition delay is %s in the eBeamScanner and the scan delay in the MPPC is "
                         "%s" % (delay, self.parent._ebeam_scanner.scanDelay.value[0]))
            return self.acqDelay.value

    def _setFilename(self, file_name):
        """
        Check if filename complies with set allowed characters
        :param file_name:
        :return:
        """
        ASM_FILE_ALLOWED_CHARS = r'[^a-z0-9_()-]'
        search = re.compile(ASM_FILE_ALLOWED_CHARS).search
        if not bool(search(file_name)):
            return file_name
        else:
            logging.warning("File_name contains invalid characters, file_name remains unchanged (only the characters "
                            "'%s' are allowed)" % ASM_FILE_ALLOWED_CHARS[2:-1])
            return self.filename.value

    def _setCellTranslation(self, cellTranslation):
        if len(cellTranslation) != self._shape[0]:
            logging.warning("An incorrect shape of the cell translation parameters is provided.\n "
                            "Please change the shape of the cell translation parameters according to the shape of the "
                            "MPPC detector.\n "
                            "Cell translation parameters remain unchanged.")
            return self.cellTranslation.value

        for row, cellTranslationRow in enumerate(cellTranslation):
            if len(cellTranslationRow) != self._shape[1]:
                logging.warning("An incorrect shape of the cell translation parameters is provided.\n"
                                "Please change the shape of the cellTranslation parameters according to the shape of "
                                "the MPPC detector.\n "
                                "Cell translation parameters remain unchanged.")
                return self.cellTranslation.value

            for column, eff_origin in enumerate(cellTranslationRow):
                if len(eff_origin) != 2:
                    logging.warning("Incorrect cell translation parameters provided, wrong number of coordinates for "
                                    "cell (%s, %s) are provided.\n"
                                    "Please provide an 'x effective origin' and an 'y effective origin' for this cell "
                                    "image.\n "
                                    "Cell translation parameters remain unchanged." %
                                    (row, column))
                    return self.cellTranslation.value

                if not isinstance(eff_origin[0], int) or not isinstance(eff_origin[1], int):
                    logging.warning("An incorrect type is used for the cell translation coordinates of cell (%s, %s).\n"
                                    "Please use type integer for both 'x effective origin' and and 'y effective "
                                    "origin' for this cell image.\n"
                                    "Type expected is: '(%s, %s)' type received '(%s, %s)'\n"
                                    "Cell translation parameters remain unchanged." %
                                    (row, column, int, int, type(eff_origin[0]), type(eff_origin[1])))
                    return self.cellTranslation.value
                elif eff_origin[0] < 0 or eff_origin[1] < 0:
                    logging.warning("Please use a minimum of 0 cell translation coordinates of cell (%s, %s).\n"
                                    "Cell translation parameters remain unchanged." %
                                    (row, column))
                    return self.cellTranslation.value

        return cellTranslation

    def _setcellDigitalGain(self, cellDigitalGain):
        if len(cellDigitalGain) != self._shape[0]:
            logging.warning("An incorrect shape of the digital gain parameters is provided. Please change the "
                            "shape of the digital gain parameters according to the shape of the MPPC detector.\n"
                            "Digital gain parameters value remain unchanged.")
            return self.cellDigitalGain.value

        for row, cellDigitalGain_row in enumerate(cellDigitalGain):
            if len(cellDigitalGain_row) != self._shape[1]:
                logging.warning("An incorrect shape of the digital gain parameters is provided.\n"
                                "Please change the shape of the digital gain parameters according to the shape of the "
                                "MPPC detector.\n "
                                "Digital gain parameters value remain unchanged.")
                return self.cellDigitalGain.value

            for column, DigitalGain in enumerate(cellDigitalGain_row):
                if not isinstance(DigitalGain, float):
                    logging.warning("An incorrect type is used for the digital gain parameters of cell (%s, %s).\n"
                                    "Please use type float for digital gain parameters for this cell image.\n"
                                    "Type expected is: '%s' type received '%s' \n"
                                    "Digital gain parameters value remain unchanged." %
                                    (row, column, float, type(DigitalGain)))
                    return self.cellDigitalGain.value
                elif DigitalGain < 0:
                    logging.warning("Please use a minimum of 0 for digital gain parameters of cell image (%s, %s).\n"
                                    "Digital gain parameters value remain unchanged." %
                                    (row, column))
                    return self.cellDigitalGain.value

        return cellDigitalGain

    def _setcellDarkOffset(self, cellDarkOffset):
        if len(cellDarkOffset) != self._shape[0]:
            logging.warning("An incorrect shape of the dark offset parameters is provided.\n"
                            "Please change the shape of the dark offset parameters according to the shape of the MPPC "
                            "detector.\n "
                            "Dark offset parameters value remain unchanged.")
            return self.cellDarkOffset.value

        for row, cellDarkOffsetRow in enumerate(cellDarkOffset):
            if len(cellDarkOffsetRow) != self._shape[1]:
                logging.warning("An incorrect shape of the dark offset parameters is provided.\n"
                                "Please change the shape of the dark offset parameters according to the shape of the "
                                "MPPC detector.\n "
                                "Dark offset parameters value remain unchanged.")
                return self.cellDarkOffset.value

            for column, DarkOffset in enumerate(cellDarkOffsetRow):
                if not isinstance(DarkOffset, int):
                    logging.warning("An incorrect type is used for the dark offset parameter of cell (%s, "
                                    "%s). \n"
                                    "Please use type integer for dark offset for this cell image.\n"
                                    "Type expected is: '%s' type received '%s' \n"
                                    "Dark offset parameters value remain unchanged." %
                                    (row, column, float, type(DarkOffset)))
                    return self.cellDarkOffset.value
                elif DarkOffset < 0:
                    logging.warning("Please use a minimum of 0 for dark offset parameters of cell image (%s, %s).\n"
                                    "Dark offset parameters value remain unchanged." %
                                    (row, column))
                    return self.cellDarkOffset.value

        return cellDarkOffset

    def get_ticks_acq_delay(self):
        """
        :return: Acq delay in number of ticks of the ebeam scanner clock frequency
        """
        return self.acqDelay.value / self.parent._ebeam_scanner.clockPeriod.value


class ASMDataFlow(model.DataFlow):
    """
    Represents the acquisition on the ASM
    """

    def __init__(self, start_func, next_func, stop_func, get_func):
        super(ASMDataFlow, self).__init__(self)

        self._start = start_func
        self._next = next_func
        self._stop = stop_func
        self._get = get_func

    def start_generate(self):
        """
        Start the dataflow using the provided function. The approriate settings are retrieved via the VA's of the
        each component
        """
        self._start()

    def next(self, field_num):
        """
        Acquire the next field image using the provided function.
        :param field_num (tuple): tuple with x,y coordinates in integers of the field 
        :return: 
        """
        self._next(field_num)

    def stop_generate(self):
        """
        Stop the dataflow using the provided function.
        """
        self._stop()

    def get(self):
        """
        Acquire a single field, can only be called if no other acquisition is active.
        :return:
        """
        if self._count_listeners() < 1:
            # Acquire and return received image
            image = self._get()
            return image

        else:
            logging.error("There is already an acquisition on going with %s listeners subscribed, first cancel/stop "
                          "current running acquisition to acquire a single field-image" % self._count_listeners())
            raise Exception("There is already an acquisition on going with %s listeners subscribed, first cancel/stop "
                            "current running acquisition to acquire a single field-image" % self._count_listeners())


class AsmApiException(Exception):
    """
    Exception for and error in the ASM API call
    """

    def __init__(self, url, response, expected_status, get_function):
        """
        Initializes exception object which defines a message based on the response available by trying to display as
        much relevant information as possible.

        :param url: URL of the call tried which was tried to make
        :param response: full/raw response from the ASM API
        :param expected_status: the expected status code
        """
        self.url = url
        self.status_code = response.status_code
        self.reason = response.reason
        self.expected_status = expected_status

        # TODO K.K. add checks for monitor parameters in ASM API
        try:
            x = 1
        except:
            logging.warning("Checking status of %s failed" % "device param")

        try:
            self.content_translated = json.loads(response.content)
            self.error_message_response(self.content_translated['status_code'],
                                        self.content_translated['message'])
        except:
            if hasattr(response, "text"):
                self.error_message_response(self.status_code, response.text)
            elif hasattr(response, "content"):
                self.error_message_response(self.status_code, response.content)
            else:
                self.empty_response()

    def __str__(self):
        return self._error

    def error_message_response(self, error_code, error_message):
        # Received bad response with an error message for the user
        self._error = ("\n"
                       "Call to %s received unexpected answer.\n"
                       "Received status code '%s' because of the reason '%s', but expected status code was'%s'\n"
                       "Error status code '%s' with the message: '%s'\n" %
                       (self.url,
                        self.status_code, self.reason, self.expected_status,
                        error_code, error_message))

    def empty_response(self):
        # Received bad response and without an error message
        self._error = ("\n"
                       "Call to %s received unexpected answer.\n"
                       "Got status code '%s' because of the reason '%s', but expected '%s'\n" %
                       (self.url,
                        self.status_code, self.reason, self.expected_status))


class TerminationRequested(Exception):
    """
    Acquisition termination requested.
    """
    pass


if __name__ == '__main__':
    # TODO K.K. remove this part after new simulator, test and code are fully implemented!
    import requests

    # Variable to differentiate "get" and "post" requests to the ASM server
    _METHOD_GET = 1
    _METHOD_POST = 2


    from PIL import ImageEnhance
    from skimage.exposure import exposure


    def display_DA_as_img(DA, thumb=False):
        """
        Function to display a DataArray on screen it also improves the contrast and other properties for better
        visibility the image
        :param DA: DataArray of the input image
        :param thumb: if set to True a thumbnail picture of 700*700 pixels is outputted
        """
        min = numpy.iinfo(DA.dtype).min
        max = numpy.iinfo(DA.dtype).max

        if DA.min() < 0:
            raise
        if min != 0:
            DA -= DA.min()
        if max != 255:
            DA = (DA / max) * 255
            DA = DA.astype('uint8')

        # Enhance image quality
        p3, p100 = numpy.percentile(DA, (3, 100))
        DA = exposure.rescale_intensity(DA, in_range=(p3, p100))

        im = Image.fromarray(DA)
        if thumb:
            im.thumbnail(size=(700, 700))

        im2 = ImageEnhance.Contrast(im)
        im2.enhance(3).show()


    def SimpleASMAPICall(url, method, expected_status, data=None, raw_response=False, timeout=600):
        """

        :param url: url of the command, server part is defined in global variable url
        :param method: getting or posting via global variables _METHOD_GET/_METHOD_POST
        :param expected_status: expected feedback of server for a positive call
        :param data: data (request body) added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response: specified the format of the structure returned
        :param timeout: [s] if within this period no bytes are received an timeout exception is raised
        :return: status_code(posting), content dictionary(getting), or entire response (raw_response=True)
        """
        logging.debug("Executing: %s" % url)
        if method == _METHOD_GET:
            resp = requests.get(url, json=data, timeout=timeout)
        elif method == _METHOD_POST:
            resp = requests.post(url, json=data, timeout=timeout)

        if resp.status_code != expected_status:
            raise AsmApiException(url, resp, expected_status)

        logging.debug("Call to %s went fine, no problems occured\n" % url)

        if raw_response:
            return resp
        elif method == _METHOD_POST:
            return resp.status_code
        elif method == _METHOD_GET:
            return json.loads(resp.content)


    # MEGA_FIELD_DATA = MegaFieldMetaData(
    #         mega_field_id=datetime.now().strftime("megafield_%Y%m%d-%H%M%S"),
    #         pixel_size=4,
    #         dwell_time=2,
    #         x_cell_size=900,
    #         x_eff_cell_size=800,
    #         y_cell_size=900,
    #         y_eff_cell_size=800,
    #         cell_parameters=[CellParameters(50, 50, 0, 1.2)] * 64,
    #         x_scan_to_acq_delay=2,
    #         x_scan_delay=0,
    #         flyback_time=0,
    #         x_scan_offset=0,
    #         y_scan_offset=0,
    #         x_scan_gain=0,
    #         y_scan_gain=0,
    #         x_descan_gain=0,
    #         y_descan_gain=0,
    #         x_descan_offset=0,
    #         y_descan_offset=0,
    #         scan_rotation=0,
    #         descan_rotation=0,
    #         y_prescan_lines=0,
    # )

    server_URL = "http://localhost:8080/v2"
    # ASMAPICall(server_URL + "/scan/clock_frequency", _METHOD_GET, 200)
    # ASMAPICall(server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)
    # ASMAPICall(_server_url + "/scan/start_mega_field", _METHOD_POST, 204, MEGA_FIELD_DATA.to_dict())
    # scan_body = FieldMetaData(position_x=0, position_y=0)
    # ASMAPICall(_server_url + "/scan/scan_field", _METHOD_POST, 204, scan_body.to_dict())
    # scan_body = FieldMetaData(position_x=6400, position_y=6400)
    # scan_body = FieldMetaData(position_x=6400*3, position_y=6400*3)
    # ASMAPICall(_server_url + "/scan/scan_field", _METHOD_POST, 204, scan_body.to_dict())
    # ASMAPICall(_server_url + "/scan/finish_mega_field", _METHOD_POST, 204)
    #
    # print("\n \n \n \n"
    #       "ended test calls at start\n"
    #       "\n")
    # time.sleep(1.0)

    logging.getLogger().setLevel(logging.DEBUG)

    CONFIG_SCANNER = {"name": "EBeamScanner", "role": "multibeam"}
    CONFIG_DESCANNER = {"name": "MirrorDescanner", "role": "galvo"}
    CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}

    ASM_manager = AcquisitionServer("ASM", "main", server_URL,
                                    children={"EBeamScanner"   : CONFIG_SCANNER,
                                              "MirrorDescanner": CONFIG_DESCANNER,
                                              "MPPC"           : CONFIG_MPPC},
                                    externalStorage={"host"     : "localhost",
                                                     "username" : "username",
                                                     "password" : "password",
                                                     "directory": "directory"}
                                    )

    for child in ASM_manager.children.value:
        if child.name == CONFIG_MPPC["name"]:
            MPPC_obj = child
        elif child.name == CONFIG_SCANNER["name"]:
            EBeamScanner_obj = child
        elif child.name == CONFIG_DESCANNER["name"]:
            MirrorDescanner_obj = child

    ASM_manager.calibrationMode.value = True
    time.sleep(0.2)
    EBeamScanner_obj.scanGain.value = (0.0, 5.0)
    time.sleep(0.2)
    ASM_manager.calibrationMode.value = False

    dwelltime = EBeamScanner_obj.dwellTime
    scan_delay = EBeamScanner_obj.scanDelay
    acq_delay = MPPC_obj.acqDelay  # NO VA YET
    MirrorDescanner_obj.scanGain.value = (10.0, 10.0)

    # MirrorDescanner_obj.getAcqSetpoints()
    # MirrorDescanner_obj._plotAcqSetpoints()

    # dwelltime.value = 100e-7
    # MPPC_obj.acqDelay.value = 0.5
    # scan_delay.value = (0, 0)
    #
    # MirrorDescanner_obj._plotAcqSetpoints()
    #
    # acq_delay.value = 0.5
    # MirrorDescanner_obj._plotAcqSetpoints()
    #
    # acq_delay.value = 0.05
    # MirrorDescanner_obj._plotAcqSetpoints()
    #
    # dwelltime.value = 2e-5
    # MirrorDescanner_obj._plotAcqSetpoints()

    image = MPPC_obj.acquire_single_field()
    # from openapi_server.models.check_mega_field_response import CheckMegaFieldResponse
    #
    # url = "/scan/check_mega_field?mega_field_id=%s&storage_directory=%s" % ('test', '/pyte')
    # resp = SimpleASMAPICall(ASM_manager._server_url + url, _METHOD_POST, 200, raw_response=True)
    # resp2 = CheckMegaFieldResponse.from_dict(resp.json())
    # print(resp2.exists)
    #
    # url = "/scan/check_mega_field?mega_field_id=%s&storage_directory=%s" % ('non_there_test', '/pyte')
    # resp = SimpleASMAPICall(ASM_manager._server_url + url, _METHOD_POST, 200, raw_response=True)
    # resp3 = CheckMegaFieldResponse.from_dict(resp.json())
    # print(resp3.exists)

    MPPC_obj.start_acquisition()

    for y in range(4):
        for x in range(4):
            MPPC_obj.get_next_field((x, y))

    MPPC_obj.stop_acquisition()
    time.sleep(5)
    ASM_manager.terminate()

    print("The END!")
