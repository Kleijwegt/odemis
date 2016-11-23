# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012-2015 Éric Piel, Delmic

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

# Provides various components which are not actually drivers but just representing
# physical components which cannot be modified by software. It's mostly used for
# computing the right metadata/behaviour of the system.

from __future__ import division

import collections
import math
from numpy.polynomial import polynomial
from odemis import model
from odemis.model import isasync
import odemis


# TODO what is the best type? Emitter? Or something else?
# Detector needs a specific .data and .shape
class OpticalLens(model.HwComponent):
    """
    A class which represents either just a lens with a given magnification,
    or the parabolic mirror and lens of a SPARC.
    It should "affect" the detector on which it's in front of.
    """
    def __init__(self, name, role, mag, na=0.95, ri=1,
                 pole_pos=None, x_max=None, hole_diam=None,
                 focus_dist=None, parabola_f=None, rotation=None, **kwargs):
        """
        name (string): should be the name of the product (for metadata)
        mag (float > 0): magnification ratio
        na (float > 0): numerical aperture
        ri (0.01 < float < 100): refractive index
        pole_pos (2 floats > 0): position of the pole on the CCD (in px, without
          binning, with the top-left pixel as origin).
          Used for angular resolved imaging on SPARC (only). cf MD_AR_POLE
        x_max (float): the distance between the parabola origin and the cutoff
          position (in meters). Used for angular resolved imaging on SPARC (only).
          cf MD_AR_XMAX
        hole_diam (float): diameter of the hole in the mirror (in meters). Used
          for angular resolved imaging on SPARC (only).
          cf MD_AR_HOLE_DIAMETER
        focus_dist (float): the vertical mirror cutoff, iow the min distance
          between the mirror and the sample (in meters). Used for angular
          resolved imaging on SPARC (only).
          cf MD_AR_FOCUS_DISTANCE
        parabola_f (float): parabola_parameter=1/4f. Used for angular
          resolved imaging on SPARC (only).
          cf MD_AR_PARABOLA_F
        rotation (0<float<2*pi): rotation between the Y axis of the SEM
          referential and the optical path axis. Used on the SPARC to report
          the rotation between the AR image and the SEM image.
        """
        assert (mag > 0)
        model.HwComponent.__init__(self, name, role, **kwargs)

        self._swVersion = "N/A (Odemis %s)" % odemis.__version__
        self._hwVersion = name

        # allow the user to modify the value, if the lens is manually changed
        self.magnification = model.FloatContinuous(mag, range=(1e-3, 1e6), unit="")
        self.numericalAperture = model.FloatContinuous(na, range=(1e-6, 1e3), unit="")
        self.refractiveIndex = model.FloatContinuous(ri, range=(0.01, 10), unit="")

        if pole_pos is not None:
            if (not isinstance(pole_pos, collections.Iterable) or
                len(pole_pos) != 2 or any(v < 0 for v in pole_pos)):
                raise ValueError("pole_pos must be 2 positive values, got %s" % pole_pos)
            self.polePosition = model.ResolutionVA(tuple(pole_pos),
                                                   rng=((0, 0), (1e6, 1e6)),
                                                   unit="px")
        if x_max is not None:
            self.xMax = model.FloatVA(x_max, unit="m")
        if hole_diam is not None:
            self.holeDiameter = model.FloatVA(hole_diam, unit="m")
        if focus_dist is not None:
            self.focusDistance = model.FloatVA(focus_dist, unit="m")
        if parabola_f is not None:
            self.parabolaF = model.FloatVA(parabola_f, unit="m")
        if rotation is not None:
            self.rotation = model.FloatContinuous(rotation, (0, 2 * math.pi),
                                                  unit="rad")


class LightFilter(model.Actuator):
    """
    A very simple class which just represent a light filter (blocks a wavelength
    band).
    It should "affect" the detector on which it's in front of, if it's filtering
    the "out" path, or "affect" the emitter in which it's after, if it's
    filtering the "in" path.
    """
    def __init__(self, name, role, band, **kwargs):
        """
        name (string): should be the name of the product (for metadata)
        band ((list of) 2-tuple of float > 0): (m) lower and higher bound of the
          wavelength of the light which goes _through_. If it's a list, it implies
          that the filter is multi-band.
        """
        # One enumerated axis: band
        # Create a 2-tuple or a set of 2-tuples
        if not isinstance(band, collections.Iterable) or len(band) == 0:
            raise ValueError("band must be a (list of a) list of 2 floats")
        # is it a list of list?
        if isinstance(band[0], collections.Iterable):
            # => set of 2-tuples
            for sb in band:
                if len(sb) != 2:
                    raise ValueError("Expected only 2 floats in band, found %d" % len(sb))
            band = tuple(tuple(b) for b in band)
        else:
            # 2-tuple
            if len(band) != 2:
                raise ValueError("Expected only 2 floats in band, found %d" % len(band))
            band = (tuple(band),)

        # Check the values are min/max and in m: typically within nm (< µm!)
        max_val = 10e-6 # m
        for low, high in band:
            if low > high:
                raise ValueError("Min of band must be first in list")
            if low > max_val or high > max_val:
                raise ValueError("Band contains very high values for light "
                     "wavelength, ensure the value is in meters: %r." % band)

        # TODO: have the position as the band value?
        band_axis = model.Axis(choices={0: band})

        model.Actuator.__init__(self, name, role, axes={"band": band_axis}, **kwargs)
        self._swVersion = "N/A (Odemis %s)" % odemis.__version__
        self._hwVersion = name

        # Will always stay at position 0
        self.position = model.VigilantAttribute({"band": 0}, readonly=True)

        # TODO: MD_OUT_WL or MD_IN_WL depending on affect
        self._metadata = {model.MD_FILTER_NAME: name,
                          model.MD_OUT_WL: band}

    def getMetadata(self):
        return self._metadata

    def moveRel(self, shift):
        return self.moveAbs(shift) # shift must be 0 => same as moveAbs

    def moveAbs(self, pos):
        if pos != {"band": 0}:
            raise ValueError("Unsupported position %s" % pos)
        return model.InstantaneousFuture()

    def stop(self, axes=None):
        pass  # nothing to stop


class Spectrograph(model.Actuator):
    """
    A very simple spectrograph component for spectrographs which cannot be
    controlled by software.
    Just provide the wavelength list describing the light position on the CCD
    according to the center wavelength specified.
    """
    def __init__(self, name, role, wlp, children=None, **kwargs):
        """
        wlp (list of floats): polynomial for conversion from distance from the
          center of the CCD to wavelength (in m):
          w = wlp[0] + wlp[1] * x + wlp[2] * x²... 
          where w is the wavelength (in m), x is the position from the center
          (in m, negative are to the left), and p is the polynomial
          (in m, m^0, m^-1...). So, typically, a first order
          polynomial contains as first element the center wavelength, and as
          second element the light dispersion (in m/m)
        """
        if kwargs.get("inverted", None):
            raise ValueError("Axis of spectrograph cannot be inverted")

        if not isinstance(wlp, list) or len(wlp) < 1:
            raise ValueError("wlp need to be a list of at least one float")

        # Note: it used to need a "ccd" child, but not anymore
        self._swVersion = "N/A (Odemis %s)" % odemis.__version__
        self._hwVersion = name

        self._wlp = wlp
        pos = {"wavelength": self._wlp[0]}
        wla = model.Axis(range=(0, 2400e-9), unit="m")
        model.Actuator.__init__(self, name, role, axes={"wavelength": wla},
                                **kwargs)
        self.position = model.VigilantAttribute(pos, unit="m", readonly=True)

    # We simulate the axis, to give the same interface as a fully controllable
    # spectrograph, but it has to actually reflect the state of the hardware.
    @isasync
    def moveRel(self, shift):
        # convert to a call to moveAbs
        new_pos = {}
        for axis, value in shift.items():
            new_pos[axis] = self.position.value[axis] + value
        return self.moveAbs(new_pos)

    @isasync
    def moveAbs(self, pos):
        for axis, value in pos.items():
            if axis == "wavelength":
                # it's read-only, so we change it via _value
                self.position._value[axis] = value
                self.position.notify(self.position.value)
            else:
                raise LookupError("Axis '%s' doesn't exist" % axis)

        return model.InstantaneousFuture()

    def stop(self, axes=None):
        # nothing to do
        pass

    def getPixelToWavelength(self, npixels, pxs):
        """
        Return the lookup table pixel number of the CCD -> wavelength observed.
        npixels (1 <= int): number of pixels on the CCD (horizontally), after
          binning.
        pxs (0 < float): pixel size in m (after binning)
        return (list of floats): pixel number -> wavelength in m
        """
        pl = list(self._wlp)
        pl[0] = self.position.value["wavelength"]

        # This polynomial is from m (distance from centre) to m (wavelength),
        # but we need from px (pixel number on spectrum) to m (wavelength). So
        # we need to convert by using the density and quantity of pixels
        # wl = pn(x)
        # x = a + bx' = pn1(x')
        # wl = pn(pn1(x')) = pnc(x')
        # => composition of polynomials
        # with "a" the distance of the centre of the left-most pixel to the
        # centre of the image, and b the density in meters per pixel.
        # distance from the pixel 0 to the centre (in m)
        distance0 = -(npixels - 1) / 2 * pxs
        pnc = self.polycomp(pl, [distance0, pxs])

        npn = polynomial.Polynomial(pnc,  # pylint: disable=E1101
                                    domain=[0, npixels - 1],
                                    window=[0, npixels - 1])
        return npn.linspace(npixels)[1]

    @staticmethod
    def polycomp(c1, c2):
        """
        Compose two polynomials : c1 o c2 = c1(c2(x))
        The arguments are sequences of coefficients, from lowest order term to highest, e.g., [1,2,3] represents the polynomial 1 + 2*x + 3*x**2.
        """
        # TODO: Polynomial(Polynomial()) seems to do just that?
        # using Horner's method to compute the result of a polynomial
        cr = [c1[-1]]
        for a in reversed(c1[:-1]):
            # cr = cr * c2 + a
            cr = polynomial.polyadd(polynomial.polymul(cr, c2), [a])

        return cr
