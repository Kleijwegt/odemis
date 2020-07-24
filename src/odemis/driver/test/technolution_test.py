# -*- coding: utf-8 -*-
"""
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

These test cases can only be done using the simulator for the ASM.
After install this can be starting using XXXXXX update this because this will change
"""
# TODO K.K. Add docstring with explanation on how to start  (latests, and stable) simulator ASM SERVER (replace XXXXXX)

import math
import os
import time
import logging
import unittest
import numpy
import matplotlib.pyplot as plt

from odemis import model

from openapi_server.models import CalibrationLoopParameters
from openapi_server.models.mega_field_meta_data import MegaFieldMetaData

from odemis.driver.technolution import AcquisitionServer, DATA_CONTENT_TO_ASM

# Export TEST_NOHW = 1 to prevent using the real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

URL = "http://localhost:8080/v2"

# Configuration of the childres of the AcquisitionServer object
CONFIG_SCANNER = {"name": "EBeamScanner", "role": "multibeam"}
CONFIG_DESCANNER = {"name": "MirrorDescanner", "role": "galvo"}
CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}
CHILDREN_ASM = {"EBeamScanner"   : CONFIG_SCANNER,
                "MirrorDescanner": CONFIG_DESCANNER,
                "MPPC"           : CONFIG_MPPC}
EXTRNAL_STORAGE = {"host"     : "localhost",
                   "username" : "username",
                   "password" : "password",
                   "directory": "directory"}


class TestAcquisitionServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM and HwCompetents present. Skipping tests.')

        cls.ASM_manager = AcquisitionServer("ASM", "main", URL, children=CHILDREN_ASM, externalStorage=EXTRNAL_STORAGE)
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        cls.ASM_manager.terminate()
        time.sleep(0.2)

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_get_API_call(self):
        clockFrequencyData = self.ASM_manager.ASM_API_Get_Call("/scan/clock_frequency", 200)
        # Check if clockFrequencyData holds the proper key
        if 'frequency' not in clockFrequencyData:
            raise IOError("Could not obtain clock frequency, received data does not hold the proper key")
        clock_freq = clockFrequencyData['frequency']

        self.assertIsInstance(clock_freq, int)

    def test_post_API_call(self):
        # Tests most basic post call, finish_mega_field (can be called multiple times without causing a problem)
        expected_status_code = 204
        status_code = self.ASM_manager.ASM_API_Post_Call("/scan/finish_mega_field", expected_status_code)
        self.assertEqual(status_code, expected_status_code)

    def test_externalStorageURL_VA(self):
        # Setting URL
        test_url = 'ftp://testname:testword@testable.com/Test_images'
        self.ASM_manager.externalStorageURL.value = test_url
        self.assertEqual(self.ASM_manager.externalStorageURL.value, test_url)

        # Test Scheme
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL.value = 'wrong://testname:testword@testable.com/Test_images'
        self.assertEqual(self.ASM_manager.externalStorageURL.value, test_url)

        # Test User
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL.value = 'ftp://wrong%user:testword@testable.com/Test_images'
        self.assertEqual(self.ASM_manager.externalStorageURL.value, test_url)

        # Test Password
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL.value = 'ftp://testname:testwrong%$word@testable.com/Test_images'
        self.assertEqual(self.ASM_manager.externalStorageURL.value, test_url)

        # Test Host
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL.value = 'ftp://testname:testword@non-test-%-able.com/Test_images'
        self.assertEqual(self.ASM_manager.externalStorageURL.value, test_url)

        # Test Path
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL.value = 'ftp://testname:testable.com/Inval!d~Path'
        self.assertEqual(self.ASM_manager.externalStorageURL.value, test_url)

    def test_calibration_loop(self):
        descanner = self.MirrorDescanner
        scanner = self.EBeamScanner
        ASM = self.ASM_manager

        # Catch errors due to an incorrect defined set of calibration parameters so it is possible to check the
        # types in the object. If the types are incorrect the same error will be found later in the same test.
        try:
            ASM.calibrationMode.value = True
        except Exception as error:
            logging.error("During activating the calibration mode the error %s occurred" % error)

        # Check types of calibration parameters (output send to the ASM) holding only primitive datatypes (int, float, string but not lists)
        calibration_parameters = ASM._calibrationParameters
        self.assertIsInstance(calibration_parameters, CalibrationLoopParameters)
        self.assertIsInstance(calibration_parameters.descan_rotation, float)
        self.assertIsInstance(calibration_parameters.x_descan_offset, int)
        self.assertIsInstance(calibration_parameters.y_descan_offset, int)
        self.assertIsInstance(calibration_parameters.dwell_time, int)
        self.assertIsInstance(calibration_parameters.scan_rotation, float)
        self.assertIsInstance(calibration_parameters.x_scan_delay, int)
        self.assertIsInstance(calibration_parameters.x_scan_offset, float)
        self.assertIsInstance(calibration_parameters.y_scan_offset, float)

        # Check descan setpoints
        self.assertIsInstance(calibration_parameters.x_descan_setpoints, list)
        self.assertIsInstance(calibration_parameters.y_descan_setpoints, list)
        for x_setpoint, y_setpoint in zip(calibration_parameters.x_descan_setpoints,
                                          calibration_parameters.y_descan_setpoints):
            self.assertIsInstance(x_setpoint, int)
            self.assertIsInstance(y_setpoint, int)

        # Check scan setpoints
        self.assertIsInstance(calibration_parameters.x_descan_setpoints, list)
        self.assertIsInstance(calibration_parameters.y_descan_setpoints, list)
        for x_setpoint, y_setpoint in zip(calibration_parameters.x_descan_setpoints,
                                          calibration_parameters.y_descan_setpoints):
            self.assertIsInstance(x_setpoint, int)
            self.assertIsInstance(y_setpoint, int)

        # Test multiple times to check if start and stopping goes correctly
        for a in range(0, 3):
            # Start calibration loop
            ASM.calibrationMode.value = True
            # Check if VA's have the correct value
            self.assertEqual(ASM.calibrationMode.value, True)
            self.assertEqual(descanner.scanGain.value, (1.0, 0.0))
            self.assertEqual(scanner.scanGain.value, (1.0, 1.0))

            # Check subscription list
            self.assertEqual(len(ASM.calibrationFrequency._listeners), 1)
            # Descanner subscriptions
            self.assertEqual(len(descanner.rotation._listeners), 1)
            self.assertEqual(len(descanner.scanOffset._listeners), 1)
            self.assertEqual(len(descanner.scanGain._listeners), 1)

            # Scanner subscriptions
            self.assertEqual(len(scanner.dwellTime._listeners), 1)
            self.assertEqual(len(scanner.rotation._listeners), 1)
            self.assertEqual(len(scanner.scanDelay._listeners), 1)
            self.assertEqual(len(scanner.dwellTime._listeners), 1)
            self.assertEqual(len(scanner.scanOffset._listeners), 1)
            self.assertEqual(len(scanner.scanGain._listeners), 1)

            # Stop calibration loop
            ASM.calibrationMode.value = False
            # Check if VA's have the correct value
            self.assertEqual(ASM.calibrationMode.value, False)
            self.assertEqual(descanner.scanGain.value, (1.0, 1.0))
            self.assertEqual(scanner.scanGain.value, (1.0, 1.0))

            # Check subscription list
            self.assertEqual(len(ASM.calibrationFrequency._listeners), 0)
            # Descanner subscriptions
            self.assertEqual(len(descanner.rotation._listeners), 0)
            self.assertEqual(len(descanner.scanOffset._listeners), 0)
            self.assertEqual(len(descanner.scanGain._listeners), 0)

            # Scanner subscriptions
            self.assertEqual(len(scanner.dwellTime._listeners), 0)
            self.assertEqual(len(scanner.rotation._listeners), 0)
            self.assertEqual(len(scanner.scanDelay._listeners), 0)
            self.assertEqual(len(scanner.dwellTime._listeners), 0)
            self.assertEqual(len(scanner.scanOffset._listeners), 0)
            self.assertEqual(len(scanner.scanGain._listeners), 0)

    def test_checkMegaFieldExists(self):
        """
        Testing basics of checkMegaFieldExists functionality.
        Note that the simulator will return for a valid input id + directory always True (Megafield exists).
        """
        ASM = self.ASM_manager

        response = ASM.checkMegaFieldExists("correct_mega_field_id", "correct_storage_dir")
        self.assertEqual(response, True)

        response = ASM.checkMegaFieldExists("wrong#@$_mega_field_id", "correct_storage_dir")
        self.assertEqual(response, False)

        response = ASM.checkMegaFieldExists("correct_mega_field_id", "wrong@#$_storage_dir")
        self.assertEqual(response, False)




class TestEBeamScanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM and HwCompetents present. Skipping tests.')

        cls.ASM_manager = AcquisitionServer("ASM", "main", URL, children=CHILDREN_ASM, externalStorage=EXTRNAL_STORAGE)
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        cls.ASM_manager.terminate()
        time.sleep(0.2)

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_clock_VAs(self):
        clockFrequencyData = self.ASM_manager.ASM_API_Get_Call("/scan/clock_frequency", 200)
        # Check if clockFrequencyData holds the proper key
        if 'frequency' not in clockFrequencyData:
            raise IOError("Could not obtain clock frequency, received data does not hold the proper key")
        clock_freq = clockFrequencyData['frequency']

        self.assertIsInstance(clock_freq, int)

        self.assertEqual(
                self.EBeamScanner.clockPeriod.value,
                1 / clock_freq)

    def test_resolution_VA(self):
        min_res = self.EBeamScanner.resolution.range[0][0]
        max_res = self.EBeamScanner.resolution.range[1][0]

        # Check if small resolution values are allowed
        self.EBeamScanner.resolution.value = (min_res + 5, min_res + 5)
        self.assertEqual(self.EBeamScanner.resolution.value, (min_res + 5, min_res + 5))

        # Check if big resolutions values are allowed
        self.EBeamScanner.resolution.value = (max_res - 200, max_res - 200)
        self.assertEqual(self.EBeamScanner.resolution.value, (max_res - 200, max_res - 200))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.resolution.value = (max_res + 10, max_res + 10)

        self.assertEqual(self.EBeamScanner.resolution.value, (max_res - 200, max_res - 200))

        with self.assertRaises(IndexError):
            self.EBeamScanner.resolution.value = (min_res - 1, min_res - 1)
        self.assertEqual(self.EBeamScanner.resolution.value, (max_res - 200, max_res - 200))

        # Check if it is allowed to have non-square resolutions
        self.EBeamScanner.resolution.value = (6000, 6500)
        self.assertEqual(self.EBeamScanner.resolution.value, (6000, 6500))

    def test_dwellTime_VA(self):
        min_dwellTime = self.EBeamScanner.dwellTime.range[0]
        max_dwellTime = self.EBeamScanner.dwellTime.range[1]

        self.EBeamScanner.dwellTime.value = 0.9 * max_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, 0.9 * max_dwellTime)

        self.EBeamScanner.dwellTime.value = min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.dwellTime.value = 1.2 * max_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

        with self.assertRaises(IndexError):
            self.EBeamScanner.dwellTime.value = 0.5 * min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

    def test_getTicksDwellTime(self):
        dwellTime = 0.9 * self.EBeamScanner.dwellTime.range[1]
        self.EBeamScanner.dwellTime.value = dwellTime
        self.assertIsInstance(self.EBeamScanner.getTicksDwellTime(), int)
        self.assertEqual(self.EBeamScanner.getTicksDwellTime(), int(dwellTime / self.EBeamScanner.clockPeriod.value))

    def test_pixelSize(self):
        min_pixelSize = self.EBeamScanner.pixelSize.range[0][0]
        max_pixelSize = self.EBeamScanner.pixelSize.range[1][0]

        # Check if small pixelSize values are allowed
        self.EBeamScanner.pixelSize.value = (min_pixelSize * 1.2, min_pixelSize * 1.2)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (min_pixelSize * 1.2, min_pixelSize * 1.2))

        # Check if big pixelSize values are allowed
        self.EBeamScanner.pixelSize.value = (max_pixelSize * 0.8, max_pixelSize * 0.8)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (max_pixelSize * 0.8, max_pixelSize * 0.8))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.pixelSize.value = (max_pixelSize * 1.6, max_pixelSize * 1.6)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (max_pixelSize * 0.8, max_pixelSize * 0.8))

        with self.assertRaises(IndexError):
            self.EBeamScanner.pixelSize.value = (min_pixelSize * 0.6, min_pixelSize * 0.6)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (max_pixelSize * 0.8, max_pixelSize * 0.8))

        # Check if setter prevents settings of non-square pixelSize
        self.EBeamScanner.pixelSize.value = (6e-7, 5e-7)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (6e-7, 6e-7))

    def test_rotation_VA(self):
        max_rotation = self.EBeamScanner.rotation.range[1]

        # Check if small rotation values are allowed
        self.EBeamScanner.rotation.value = 0.1 * max_rotation
        self.assertEqual(self.EBeamScanner.rotation.value, 0.1 * max_rotation)

        # Check if big rotation values are allowed
        self.EBeamScanner.rotation.value = 0.9 * max_rotation
        self.assertEqual(self.EBeamScanner.rotation.value, 0.9 * max_rotation)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.rotation.value = 1.1 * max_rotation
        self.assertEqual(self.EBeamScanner.rotation.value, 0.9 * max_rotation)

        with self.assertRaises(IndexError):
            self.EBeamScanner.rotation.value = (-0.1 * max_rotation)
        self.assertEqual(self.EBeamScanner.rotation.value, 0.9 * max_rotation)

    def test_scanOffset_VA(self):
        min_scanOffset = self.EBeamScanner.scanOffset.range[0][0]
        max_scanOffset = self.EBeamScanner.scanOffset.range[1][0]

        # Check if small scanOffset values are allowed
        self.EBeamScanner.scanOffset.value = (0.1 * max_scanOffset, 0.1 * max_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.1 * max_scanOffset, 0.1 * max_scanOffset))

        # Check if big scanOffset values are allowed
        self.EBeamScanner.scanOffset.value = (0.9 * max_scanOffset, 0.9 * max_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanOffset.value = (1.2 * max_scanOffset, 1.2 * max_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanOffset.value = (1.2 * min_scanOffset, 1.2 * min_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

    def test_scanGain_VA(self):
        min_scanGain = self.EBeamScanner.scanGain.range[0][0]
        max_scanGain = self.EBeamScanner.scanGain.range[1][0]

        # Check if small scanGain values are allowed
        self.EBeamScanner.scanGain.value = (0.1 * max_scanGain, 0.1 * max_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.1 * max_scanGain, 0.1 * max_scanGain))

        # Check if big scanGain values are allowed
        self.EBeamScanner.scanGain.value = (0.9 * max_scanGain, 0.9 * max_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanGain.value = (1.2 * max_scanGain, 1.2 * max_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanGain.value = (1.2 * min_scanGain, 1.2 * min_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

    def test_scanDelay_VA(self):
        min_scanDelay = self.EBeamScanner.scanDelay.range[0][0]
        max_scanDelay = self.EBeamScanner.scanDelay.range[1][0]
        min_y_prescan_lines = self.EBeamScanner.scanDelay.range[0][1]
        max_y_prescan_lines = self.EBeamScanner.scanDelay.range[1][1]

        # set _mppc.acqDelay > max_scanDelay to allow all options to be set
        self.MPPC.acqDelay.value = self.MPPC.acqDelay.range[1]

        # Check if small scanDelay values are allowed
        self.EBeamScanner.scanDelay.value = (0.1 * max_scanDelay, 0.1 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.1 * max_scanDelay, 0.1 * max_y_prescan_lines))

        # Check if big scanDelay values are allowed
        self.EBeamScanner.scanDelay.value = (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanDelay.value = (1.2 * max_scanDelay, 1.2 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanDelay.value = (-0.2 * max_scanDelay, -0.2 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines))

        # Check if setter prevents from setting negative values for self.EBeamScanner.parent._mppc.acqDelay.value - self.EBeamScanner.scanDelay.value[0]
        self.EBeamScanner.scanDelay.value = (min_scanDelay, min_y_prescan_lines)
        self.EBeamScanner.parent._mppc.acqDelay.value = 0.5 * max_scanDelay
        self.EBeamScanner.scanDelay.value = (0.6 * max_scanDelay, 0.6 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (min_scanDelay, min_y_prescan_lines))


class TestMirrorDescanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM and HwComponents present. Skipping tests.')

        cls.ASM_manager = AcquisitionServer("ASM", "main", URL, children=CHILDREN_ASM, externalStorage=EXTRNAL_STORAGE)
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        cls.ASM_manager.terminate()
        time.sleep(0.2)

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_rotation_VA(self):
        max_rotation = self.MirrorDescanner.rotation.range[1]

        # Check if small rotation values are allowed
        self.MirrorDescanner.rotation.value = 0.1 * max_rotation
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.1 * max_rotation)

        # Check if big rotation values are allowed
        self.MirrorDescanner.rotation.value = 0.9 * max_rotation
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.9 * max_rotation)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.rotation.value = 1.1 * max_rotation
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.9 * max_rotation)

        with self.assertRaises(IndexError):
            self.MirrorDescanner.rotation.value = (-0.1 * max_rotation)
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.9 * max_rotation)

    def test_scanOffset_VA(self):
        min_scanOffset = self.MirrorDescanner.scanOffset.range[0][0]
        max_scanOffset = self.MirrorDescanner.scanOffset.range[1][0]

        # Check if small scanOffset values are allowed
        self.MirrorDescanner.scanOffset.value = (int(0.1 * max_scanOffset), int(0.1 * max_scanOffset))
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (int(0.1 * max_scanOffset), int(0.1 * max_scanOffset)))

        # Check if big scanOffset values are allowed
        self.MirrorDescanner.scanOffset.value = (int(0.9 * max_scanOffset), int(0.9 * max_scanOffset))
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (int(0.9 * max_scanOffset), int(0.9 * max_scanOffset)))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanOffset.value = (int(1.2 * max_scanOffset), int(1.2 * max_scanOffset))
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (int(0.9 * max_scanOffset), int(0.9 * max_scanOffset)))

        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanOffset.value = (int(1.2 * min_scanOffset), int(1.2 * min_scanOffset))
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (int(0.9 * max_scanOffset), int(0.9 * max_scanOffset)))

        with self.assertRaises(TypeError):
            self.MirrorDescanner.scanOffset.value = (int(0.5 * max_scanOffset) + 0.01, int(0.5 * max_scanOffset) + 0.01)
        self.assertIsInstance(self.MirrorDescanner.scanOffset.value[0], int)
        self.assertIsInstance(self.MirrorDescanner.scanOffset.value[1], int)
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (int(0.9 * max_scanOffset), int(0.9 * max_scanOffset)))

    def test_scanGain_VA(self):
        min_scanGain = self.MirrorDescanner.scanGain.range[0][0]
        max_scanGain = self.MirrorDescanner.scanGain.range[1][0]

        # Check if small scanGain values are allowed
        self.MirrorDescanner.scanGain.value = (0.1 * max_scanGain, 0.1 * max_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.1 * max_scanGain, 0.1 * max_scanGain))

        # Check if big scanGain values are allowed
        self.MirrorDescanner.scanGain.value = (0.9 * max_scanGain, 0.9 * max_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanGain.value = (1.2 * max_scanGain, 1.2 * max_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanGain.value = (1.2 * min_scanGain, 1.2 * min_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

    def test_getXAcqSetpoints(self):
        """
        After each change the same properties of the testcases are checked, first the length (total number of setpoints)
        then the expected range of the values are checked. The maximum is not necessarily reached because this is
        dependent on the descan_period/dwell_time
        """

        def expected_setpoint_length(dwellTime, physcicalFlybackTime, X_cell_size, descan_period):
            scanning_setpoints = math.ceil((dwellTime * X_cell_size) / descan_period)
            flyback_setpoints = math.ceil(physcicalFlybackTime / descan_period)
            return scanning_setpoints + flyback_setpoints

        def expected_setpoint_range(X_scan_offset, X_cell_size, X_scan_gain):
            lowest_expected_value = X_scan_offset - 0.5 * X_cell_size * X_scan_gain
            highest_expected_value = X_scan_offset + 0.5 * X_cell_size * X_scan_gain
            return int(lowest_expected_value), int(highest_expected_value)

        # Check default values
        X_descan_setpoints = self.MirrorDescanner.getXAcqSetpoints()
        self.assertEqual(len(X_descan_setpoints),
                         expected_setpoint_length(self.EBeamScanner.dwellTime.value,
                                                  self.MirrorDescanner.physicalFlybackTime,
                                                  self.MPPC.cellCompleteResolution.value[0],
                                                  self.MirrorDescanner.clockPeriod.value))
        setpoint_range = expected_setpoint_range(self.MirrorDescanner.scanOffset.value[0],
                                                 self.MPPC.cellCompleteResolution.value[0],
                                                 self.MirrorDescanner.scanGain.value[0])
        self.assertEqual(min(X_descan_setpoints), setpoint_range[0])
        # Not said that during rise/scan phase the maximum is actually reached, only for very big dwell times this is so
        self.assertLessEqual(max(X_descan_setpoints), setpoint_range[1])

        # Check with changing the dwell_time
        self.EBeamScanner.dwellTime.value = 4e-6
        X_descan_setpoints = self.MirrorDescanner.getXAcqSetpoints()
        self.assertEqual(len(X_descan_setpoints),
                         expected_setpoint_length(self.EBeamScanner.dwellTime.value,
                                                  self.MirrorDescanner.physicalFlybackTime,
                                                  self.MPPC.cellCompleteResolution.value[0],
                                                  self.MirrorDescanner.clockPeriod.value))
        setpoint_range = expected_setpoint_range(self.MirrorDescanner.scanOffset.value[0],
                                                 self.MPPC.cellCompleteResolution.value[0],
                                                 self.MirrorDescanner.scanGain.value[0])
        self.assertEqual(min(X_descan_setpoints), setpoint_range[0])
        self.assertLessEqual(max(X_descan_setpoints), setpoint_range[1])

    def test_getYAcqSetpoints(self):
        """
        After each change the same properties of the testcases are checked, first the range of the values in the Y
        setpouints is checked, then the length (total number of setpoints) is checked.
        """

        def expected_setpoint_range(Y_scan_offset, Y_cell_size, Y_scan_gain):
            lowest_expected_value = Y_scan_offset - 0.5 * Y_cell_size * Y_scan_gain
            # (Y_cell_size - 2) because like the 'range' command the last increment should not be used (-2 because
            # the multiplication by 0.5)
            highest_expected_value = Y_scan_offset + 0.5 * (Y_cell_size - 2) * Y_scan_gain
            return int(lowest_expected_value), int(highest_expected_value)

        # Check default values
        Y_descan_setpoints = self.MirrorDescanner.getYAcqSetpoints()
        self.assertEqual((min(Y_descan_setpoints), max(Y_descan_setpoints)),
                         expected_setpoint_range(self.MirrorDescanner.scanOffset.value[1],
                                                 self.MPPC.cellCompleteResolution.value[1],
                                                 self.MirrorDescanner.scanGain.value[1]))
        self.assertEqual(len(Y_descan_setpoints), self.MPPC.cellCompleteResolution.value[1])

        # Check with changing gain
        self.MirrorDescanner.scanGain.value = (10.0, 7.0)  # only change Y value to a prime number
        Y_descan_setpoints = self.MirrorDescanner.getYAcqSetpoints()
        self.assertEqual((min(Y_descan_setpoints), max(Y_descan_setpoints)),
                         expected_setpoint_range(self.MirrorDescanner.scanOffset.value[1],
                                                 self.MPPC.cellCompleteResolution.value[1],
                                                 self.MirrorDescanner.scanGain.value[1]))
        self.assertEqual(len(Y_descan_setpoints), self.MPPC.cellCompleteResolution.value[1])

        # Change the cell_size
        self.MPPC.cellCompleteResolution.value = (777, 777)  # only change Y value to a prime number
        Y_descan_setpoints = self.MirrorDescanner.getYAcqSetpoints()
        self.assertEqual((min(Y_descan_setpoints), max(Y_descan_setpoints)),
                         expected_setpoint_range(self.MirrorDescanner.scanOffset.value[1],
                                                 self.MPPC.cellCompleteResolution.value[1],
                                                 self.MirrorDescanner.scanGain.value[1]))
        self.assertEqual(len(Y_descan_setpoints), self.MPPC.cellCompleteResolution.value[1])

    @unittest.skip  # Skip plotting acq setpoints
    def test_plot_getAcqSetpoints(self):
        """
        Test case for inspecting global behaviour of the setpoint profiles.
        """
        self.EBeamScanner.dwellTime.value = 4e-6  # Increase dwell time to see steps in the profile better
        self.MirrorDescanner.physicalFlybackTime = 25e-4  # Increase flybacktime to see its effect in the profile better

        X_descan_setpoints = self.MirrorDescanner.getXAcqSetpoints()
        Y_descan_setpoints = self.MirrorDescanner.getYAcqSetpoints()

        fig, axs = plt.subplots(2)
        axs[0].plot(numpy.tile(X_descan_setpoints[::], 4), "xb")
        axs[1].plot(Y_descan_setpoints[::], "or")
        plt.show()


class TestMPPC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM and HwCompetents present. Skipping tests.')

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.ASM_manager = AcquisitionServer("ASM", "main", URL, children=CHILDREN_ASM, externalStorage=EXTRNAL_STORAGE)
        for child in self.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                self.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                self.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                self.MirrorDescanner = child

    def tearDown(self):
        self.ASM_manager.terminate()
        time.sleep(0.2)

    def test_file_name_VA(self):
        self.MPPC.filename.value = "testing_file_name"
        self.assertEqual(self.MPPC.filename.value, "testing_file_name")
        self.MPPC.filename.value = "@testing_file_name"
        self.assertEqual(self.MPPC.filename.value, "testing_file_name")

    def test_acqDelay_VA(self):
        # Set _mppc.acqDelay > max_scanDelay to allow all values to be set
        max_acqDelay = self.MPPC.acqDelay.range[1]

        # Check if big acqDelay values are allowed
        self.MPPC.acqDelay.value = 0.9 * max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, 0.9 * max_acqDelay)

        # Lower EBeamScanner scanDelay value so that acqDelay can be changed freely
        self.EBeamScanner.scanDelay.value = (2e-10, 2e-10)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (2e-10, 2e-10))

        # Check if small acqDelay values are allowed
        self.MPPC.acqDelay.value = 0.1 * max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, 0.1 * max_acqDelay)

        # Change EBeamScanner scanDelay value so that acqDelay can be changed (first change acqDelay to allow this)
        self.MPPC.acqDelay.value = max_acqDelay
        self.EBeamScanner.scanDelay.value = self.EBeamScanner.scanDelay.range[1]

        # Check if setter prevents from setting negative values for self.MPPC.acqDelay.value -
        self.MPPC.acqDelay.value = 0.5 * max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, max_acqDelay)
        self.assertEqual(self.EBeamScanner.scanDelay.value, self.EBeamScanner.scanDelay.range[1])

    def test_dataContentVA(self):
        for key in DATA_CONTENT_TO_ASM:
            self.MPPC.dataContent.value = key
            self.assertEqual(self.MPPC.dataContent.value, key)

        # Test incorrect input
        with self.assertRaises(IndexError):
            self.MPPC.dataContent.value = "incorrect input"
        self.assertEqual(self.MPPC.dataContent.value, key)  # Check if variable remains unchanged

    def test_cellTranslation(self):
        # Testing assigning of different values. Which are chosen so that the value corresponds with the placement in
        # the tuple
        self.MPPC.cellTranslation.value = \
            tuple(tuple((10 + j, 20 + j) for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.assertEqual(
                self.MPPC.cellTranslation.value,
                tuple(tuple((10 + j, 20 + j) for j in range(i, i + self.MPPC.shape[0]))
                      for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0])))

        # Changing the digital gain back to something simple
        self.MPPC.cellTranslation.value = tuple(
                tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1]))

        # Test missing rows
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0] - 1))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        # Test missing column
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1] - 1))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        # Test wrong number of coordinates
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(
                    tuple((50) for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, 50, 50) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        # Test wrong type
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50.0, 50) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, 50.0) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((-1, 50) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, -1) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

    def test_celldarkOffset(self):
        # Testing assigning different values. Which are chosen so that the value corresponds with the placement in
        # the tuple
        self.MPPC.cellDarkOffset.value = \
            tuple(tuple(j for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.assertEqual(
                self.MPPC.cellDarkOffset.value,
                tuple(tuple(j for j in range(i, i + self.MPPC.shape[0]))
                      for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))
        )

        # Changing the digital gain back to something simple
        self.MPPC.cellDarkOffset.value = tuple(
                tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1]))

        # Test missing rows
        with self.assertRaises(ValueError):
            self.MPPC.cellDarkOffset.value = tuple(tuple(0 for i in range(0, self.MPPC.shape[0] - 1))
                                                   for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDarkOffset.value,
                         tuple(tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test missing column
        with self.assertRaises(ValueError):
            self.MPPC.cellDarkOffset.value = tuple(tuple(0 for i in range(0, self.MPPC.shape[0]))
                                                   for i in range(0, self.MPPC.shape[1] - 1))
        self.assertEqual(self.MPPC.cellDarkOffset.value,
                         tuple(tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test wrong type
        with self.assertRaises(ValueError):
            self.MPPC.cellDarkOffset.value = tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0]))
                                                   for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDarkOffset.value,
                         tuple(tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test minimum value setter
        with self.assertRaises(ValueError):
            self.MPPC.cellDarkOffset.value = tuple(tuple(-1 for i in range(0, self.MPPC.shape[0]))
                                                   for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDarkOffset.value,
                         tuple(tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

    def test_cellDigitalGain(self):
        # Testing assigning of different values. Which are chosen so that the value corresponds with the placement in
        # the tuple
        self.MPPC.cellDigitalGain.value = \
            tuple(tuple(float(j) for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.assertEqual(
                self.MPPC.cellDigitalGain.value,
                tuple(tuple(float(j) for j in range(i, i + self.MPPC.shape[0]))
                      for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))
        )

        # Changing the digital gain back to something simple
        self.MPPC.cellDigitalGain.value = tuple(
                tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1]))

        # Test missing rows
        with self.assertRaises(ValueError):
            self.MPPC.cellDigitalGain.value = tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0] - 1))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test missing column
        with self.assertRaises(ValueError):
            self.MPPC.cellDigitalGain.value = tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1] - 1))
        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test int as type (should be converted)
        self.MPPC.cellDigitalGain.value = tuple(
                tuple(int(0) for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test invalid type
        with self.assertRaises(ValueError):
            self.MPPC.cellDigitalGain.value = tuple(tuple('string_type' for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test minimum value setter
        with self.assertRaises(ValueError):
            self.MPPC.cellDigitalGain.value = tuple(tuple(-1.0 for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

    def test_cellCompleteResolution(self):
        min_res = self.MPPC.cellCompleteResolution.range[0][0]
        max_res = self.MPPC.cellCompleteResolution.range[1][0]

        # Check if small resolution values are allowed
        self.MPPC.cellCompleteResolution.value = (min_res + 5, min_res + 5)
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (min_res + 5, min_res + 5))

        # Check if big resolutions values are allowed
        self.MPPC.cellCompleteResolution.value = (max_res - 200, max_res - 200)
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (max_res - 200, max_res - 200))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MPPC.cellCompleteResolution.value = (max_res + 10, max_res + 10)

        self.assertEqual(self.MPPC.cellCompleteResolution.value, (max_res - 200, max_res - 200))

        with self.assertRaises(IndexError):
            self.MPPC.cellCompleteResolution.value = (min_res - 1, min_res - 1)
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (max_res - 200, max_res - 200))

        # Check if setter prevents settings of non-square resolutions
        self.MPPC.cellCompleteResolution.value = (int(0.2 * max_res), int(0.5 * max_res))
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (int(0.2 * max_res), int(0.5 * max_res)))

    def test_assemble_megafield_metadata(self):
        """
        Test which checks the MegaFieldMetadata object and the correctly ordering (row/column conversions) from the
        VA's to the MegaFieldMetadata object which is passed to the ASM
        """
        megafield_metadata = self.MPPC._assemble_megafield_metadata()
        self.assertIsInstance(megafield_metadata, MegaFieldMetaData)

        # Test attributes megafield_metadata holding only primitive datatypes (int, float, string but not lists)
        self.assertIsInstance(megafield_metadata.mega_field_id, str)
        self.assertIsInstance(megafield_metadata.storage_directory, str)
        self.assertIsInstance(megafield_metadata.custom_data, str)
        self.assertIsInstance(megafield_metadata.stage_position_x, float)
        self.assertIsInstance(megafield_metadata.stage_position_x, float)
        self.assertIsInstance(megafield_metadata.pixel_size, int)
        self.assertIsInstance(megafield_metadata.dwell_time, int)
        self.assertIsInstance(megafield_metadata.x_scan_to_acq_delay, int)
        self.assertIsInstance(megafield_metadata.x_cell_size, int)
        self.assertIsInstance(megafield_metadata.x_eff_cell_size, int)
        self.assertIsInstance(megafield_metadata.x_scan_gain, float)
        self.assertIsInstance(megafield_metadata.x_scan_offset, float)
        self.assertIsInstance(megafield_metadata.x_descan_offset, int)
        self.assertIsInstance(megafield_metadata.y_cell_size, int)
        self.assertIsInstance(megafield_metadata.y_eff_cell_size, int)
        self.assertIsInstance(megafield_metadata.y_scan_gain, float)
        self.assertIsInstance(megafield_metadata.y_scan_offset, float)
        self.assertIsInstance(megafield_metadata.y_descan_offset, int)
        self.assertIsInstance(megafield_metadata.y_prescan_lines, int)
        self.assertIsInstance(megafield_metadata.x_scan_delay, int)
        self.assertIsInstance(megafield_metadata.scan_rotation, float)
        self.assertIsInstance(megafield_metadata.descan_rotation, float)

        # Test descan setpoints
        self.assertIsInstance(megafield_metadata.x_descan_setpoints, list)
        self.assertIsInstance(megafield_metadata.y_descan_setpoints, list)
        for x_setpoint, y_setpoint in zip(megafield_metadata.x_descan_setpoints, megafield_metadata.y_descan_setpoints):
            self.assertIsInstance(x_setpoint, int)
            self.assertIsInstance(y_setpoint, int)

        # Test cell_parameters
        self.MPPC.cellTranslation.value = \
            tuple(tuple((10 + j, 20 + j) for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.MPPC.cellDarkOffset.value = \
            tuple(tuple(j for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.MPPC.cellDigitalGain.value = \
            tuple(tuple(float(j) for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        megafield_metadata = self.MPPC._assemble_megafield_metadata()
        self.assertEqual(len(megafield_metadata.cell_parameters), self.MPPC.shape[0] * self.MPPC.shape[1])

        for cell_number, individual_cell in enumerate(megafield_metadata.cell_parameters):
            self.assertEqual(individual_cell.digital_gain, cell_number)
            self.assertEqual(individual_cell.x_eff_orig, 10 + cell_number)
            self.assertEqual(individual_cell.y_eff_orig, 20 + cell_number)
            self.assertIsInstance(individual_cell.digital_gain, float)
            self.assertIsInstance(individual_cell.x_eff_orig, int)
            self.assertIsInstance(individual_cell.y_eff_orig, int)

        # Test multiple stage positions and check if both ints and floats works
        stage_pos_generator = ((float(i), j) for i in range(0, 5) for j in range(0, 5))
        for stage_pos in stage_pos_generator:
            self.MPPC._metadata[model.MD_POS] = stage_pos
            megafield_metadata = self.MPPC._assemble_megafield_metadata()
            self.assertEqual(megafield_metadata.stage_position_x, stage_pos[0])
            self.assertEqual(megafield_metadata.stage_position_y, stage_pos[1])
            self.assertIsInstance(megafield_metadata.stage_position_x, float)
            self.assertIsInstance(megafield_metadata.stage_position_x, float)


class Test_ASMDataFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM and HwCompetents present. Skipping tests.')

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.ASM_manager = AcquisitionServer("ASM", "main", URL, children=CHILDREN_ASM, externalStorage=EXTRNAL_STORAGE)
        for child in self.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                self.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                self.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                self.MirrorDescanner = child

        # Ensure that only empty images will be received
        self.MPPC.dataContent.value = "empty"

    def tearDown(self):
        self.MPPC.data.unsubscribe(self.image_received)
        self.MPPC.data.unsubscribe(self.image_2_received)
        if len(self.MPPC.data._listeners) > 0:
            raise IOError("Listeners are not correctly unsubscribed")
        self.ASM_manager.terminate()
        time.sleep(0.2)

    def image_received(self, *args):
        """
        Subscriber for test cases which counts the number time it is notified
        """
        self.counter += 1
        print("image received")

    def image_2_received(self, *args):
        """
        Subscriber for test cases which counts the number time it is notified
        """
        self.counter2 += 1
        print("image two received")

    def test_get_field(self):
        dataflow = self.MPPC.data

        image = dataflow.get()
        self.assertIsInstance(image, model.DataArray)

    def test_subscribe_get_field(self):
        dataflow = self.MPPC.data

        image = dataflow.get()
        self.assertIsInstance(image, model.DataArray)

        dataflow.subscribe(self.image_received)
        with self.assertRaises(Exception):
            # Check that image is not received if already on subscriber is present
            image = dataflow.get()

        dataflow.unsubscribe(self.image_received)

    def test_subscribe_mega_field(self):
        field_images = (3, 4)
        self.counter = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))

        time.sleep(field_images[0] * field_images[1])
        dataflow.unsubscribe(self.image_received)
        time.sleep(0.5)
        self.assertEqual(field_images[0] * field_images[1], self.counter)

    def test_dataContent_received(self):
        """
        Tests if the appropriate image size is returned after calling with empty, thumbnail or full image as
        datacontent by using the get field image method.
        """
        data_content_size = {"empty": (1, 1), "thumbnail": (100, 100), "full": self.EBeamScanner.resolution.value}

        for key, value in DATA_CONTENT_TO_ASM.items():
            dataflow = self.MPPC.data
            image = dataflow.get(dataContent=key)
            self.assertIsInstance(image, model.DataArray)
            self.assertEqual(image.shape, data_content_size[key])

    def test_terminate(self):
        field_images = (3, 4)
        termination_point = (1, 3)
        self.counter = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                if x == termination_point[0] and y == termination_point[1]:
                    print("Send terminating command")
                    self.MPPC.terminate()
                    time.sleep(1.5)
                    self.assertEqual(self.MPPC.acq_queue.qsize(), 0,
                                     "Queue was not cleared properly and is not empty")
                    time.sleep(0.5)

                dataflow.next((x, y))
                time.sleep(1.5)

        self.assertEqual(self.MPPC._acq_thread.is_alive(), False)
        self.assertEqual((termination_point[0] * field_images[1]) + termination_point[1], self.counter)
        dataflow.unsubscribe(self.image_received)

    def test_restart_acquistion(self):
        field_images = (3, 4)
        termination_point = (1, 3)
        self.counter = 0
        self.counter2 = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                if x == termination_point[0] and y == termination_point[1]:
                    print("Send terminating command")
                    self.MPPC.terminate()
                    time.sleep(1.5)
                    self.assertEqual(self.MPPC.acq_queue.qsize(), 0,
                                     "Queue was not cleared properly and is not empty")
                    time.sleep(0.5)

                dataflow.next((x, y))
                time.sleep(1.5)

        self.assertEqual(self.MPPC._acq_thread.is_alive(), False)
        self.assertEqual((termination_point[0] * field_images[1]) + termination_point[1], self.counter)
        dataflow.unsubscribe(self.image_received)

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_2_received)
        self.assertEqual(self.MPPC._acq_thread.is_alive(), True)
        self.assertEqual(self.MPPC.acq_queue.qsize(), 1,
                         "Queue was not cleared properly and is not empty")
        dataflow.next((0, 0))
        time.sleep(1.5)
        self.assertEqual(1, self.counter2)
        self.assertEqual((termination_point[0] * field_images[1]) + termination_point[1], self.counter)

    def test_two_folowing_mega_fields(self):
        field_images = (3, 4)
        self.counter = 0
        self.counter2 = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))

        time.sleep(field_images[0] * field_images[1])
        self.assertEqual(field_images[0] * field_images[1], self.counter)

        # Start acquiring second megafield
        dataflow.subscribe(self.image_2_received)
        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))

        time.sleep(field_images[0] * field_images[1])
        dataflow.unsubscribe(self.image_received)
        dataflow.unsubscribe(self.image_2_received)
        time.sleep(0.5)
        self.assertEqual(2 * field_images[0] * field_images[1], self.counter)  # Test subscriber first megafield
        self.assertEqual(field_images[0] * field_images[1], self.counter2)  # Test subscriber second megafield

    def test_multiple_subscriptions(self):
        field_images = (3, 4)
        self.counter = 0
        self.counter2 = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)
        dataflow.subscribe(self.image_2_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))

        time.sleep(field_images[0] * field_images[1])
        dataflow.unsubscribe(self.image_received)
        dataflow.unsubscribe(self.image_2_received)
        time.sleep(0.5)
        self.assertEqual(field_images[0] * field_images[1], self.counter)
        self.assertEqual(field_images[0] * field_images[1], self.counter2)

    def test_late_subscription(self):
        field_images = (3, 4)
        add_second_subscription = (1, 3)
        self.counter = 0
        self.counter2 = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                if x == add_second_subscription[0] and y == add_second_subscription[1]:
                    # Wait until all the old items in the que are handled so the outcome of the first counter is known
                    print("Adding second subscription")
                    dataflow.subscribe(self.image_2_received)
                dataflow.next((x, y))
                time.sleep(1.5)

        dataflow.unsubscribe(self.image_received)
        dataflow.unsubscribe(self.image_2_received)
        time.sleep(0.5)
        self.assertEqual(field_images[0] * field_images[1], self.counter)  # Check early subscriber
        self.assertEqual(
                ((field_images[1] - add_second_subscription[1]) * field_images[0])
                + field_images[0] - add_second_subscription[0],
                self.counter2)  # Check late subscriber

    def test_get_field_and_mega_field_combination(self):
        field_images = (3, 4)
        global counter, counter2
        self.counter = 0
        self.counter2 = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))

        # Acquire single field without unsubscribing listener (expect error)
        with self.assertRaises(Exception):
            image = dataflow.get()
            self.assertIsInstance(image, model.DataArray)

        time.sleep(field_images[0] * field_images[1])
        self.assertEqual(field_images[0] * field_images[1], self.counter)
        dataflow.unsubscribe(self.image_received)

        # Acquire single field after unsubscribing listener
        image = dataflow.get()
        self.assertIsInstance(image, model.DataArray)

        # Start acquiring second mega field
        dataflow.subscribe(self.image_2_received)
        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))

        time.sleep(field_images[0] * field_images[1])
        dataflow.unsubscribe(self.image_2_received)
        time.sleep(0.5)
        self.assertEqual(field_images[0] * field_images[1], self.counter)
        self.assertEqual(field_images[0] * field_images[1], self.counter2)


if __name__ == '__main__':
    # Set logger level to debug to observe all the output (useful when a test fails)
    logging.getLogger().setLevel(logging.DEBUG)
    unittest.main()
