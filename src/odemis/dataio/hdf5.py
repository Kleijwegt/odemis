# -*- coding: utf-8 -*-
'''
Created on 14 Jan 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division
from odemis import model
from datetime import datetime
import h5py
import numpy
import os
import time
# User-friendly name
FORMAT = "HDF5"
# list of file-name extensions possible, the first one is the default when saving a file 
EXTENSIONS = [".h5", ".hdf5"]

# We are trying to follow the same format as SVI, as defined here:
# http://www.svi.nl/HDF5
# A file follows this structure:
# + /
#   + Preview (this our extension, to contain thumbnails)
#     + RGB image (*) (HDF5 Image with Dimension Scales)
#     + DimensionScale*
#     + *Offset (position on the axis)
#   + AcquisitionName (one per set of emitter/detector) 
#     + ImageData
#       + Image (HDF5 Image with Dimension Scales CTZXY)
#       + DimensionScale*
#       + *Offset (position on the axis)
#     + PhysicalData
#     + SVIData (Not necessary for us)


# Image is an official extension to HDF5:
# http://www.hdfgroup.org/HDF5/doc/ADGuide/ImageSpec.html


# h5py doesn't implement explicitly HDF5 image, and is not willing to cf:
# http://code.google.com/p/h5py/issues/detail?id=157
def _create_image_dataset(group, dataset_name, image, **kwargs):
    """
    Create a dataset respecting the HDF5 image specification
    http://www.hdfgroup.org/HDF5/doc/ADGuide/ImageSpec.html
   
    group (HDF group): the group that will contain the dataset
    dataset_name (string): name of the dataset
    image (numpy.ndimage): the image to create. It should have at least 2 dimensions
    returns the new dataset
    """
    assert(len(image.shape) >= 2)
    image_dataset = group.create_dataset(dataset_name, data=image, **kwargs)
       
    # numpy.string_ is to force fixed-length string (necessary for compatibility)
    image_dataset.attrs["CLASS"] = numpy.string_("IMAGE")
    # Colour image?
    if len(image.shape) == 3 and (image.shape[0] == 3 or image.shape[2] == 3):
        # TODO: check dtype is int?
        image_dataset.attrs["IMAGE_SUBCLASS"] = numpy.string_("IMAGE_TRUECOLOR")
        image_dataset.attrs["IMAGE_COLORMODEL"] = numpy.string_("RGB")
        if image.shape[0] == 3:
            # Stored as [pixel components][height][width]
            image_dataset.attrs["INTERLACE_MODE"] = numpy.string_("INTERLACE_PLANE")
        else: # This is the numpy standard
            # Stored as [height][width][pixel components]
            image_dataset.attrs["INTERLACE_MODE"] = numpy.string_("INTERLACE_PIXEL")
    else:
        image_dataset.attrs["IMAGE_SUBCLASS"] = numpy.string_("IMAGE_GRAYSCALE")
        image_dataset.attrs["IMAGE_WHITE_IS_ZERO"] = numpy.array(0, dtype="uint8")
        idtype = numpy.iinfo(image.dtype)
        image_dataset.attrs["IMAGE_MINMAXRANGE"] = [idtype.min, idtype.max]
    
    image_dataset.attrs["DISPLAY_ORIGIN"] = numpy.string_("UL") # not rotated
    image_dataset.attrs["IMAGE_VERSION"] = numpy.string_("1.2")
   
    return image_dataset

def _add_image_info(group, dataset, image):
    """
    Adds the basic metadata information about an image (scale and offset)
    group (HDF Group): the group that contains the dataset
    dataset (HDF Dataset): the image dataset
    image (DataArray >= 2D): image with metadata, the last 2 dimensions are Y and X (H,W)
    """
    # Note: DimensionScale support is only part of h5py since v2.1
    # Dimensions
    # The order of the dimension is reversed (the slowest changing is last)
    l = len(dataset.dims)
    dataset.dims[l-1].label = "X"
    dataset.dims[l-2].label = "Y"
    # support more dimensions if available:
    if l >= 3:
        dataset.dims[l-3].label = "Z"
    if l >= 4:
        dataset.dims[l-4].label = "T"
    if l >= 5:
        dataset.dims[l-5].label = "C"
    
    # Offset
    if model.MD_POS in image.metadata:
        pos = image.metadata[model.MD_POS]
        group["XOffset"] = pos[0]
        group["XOffset"].attrs["UNIT"] = "m" # our extension
        group["YOffset"] = pos[1]
        group["YOffset"].attrs["UNIT"] = "m" # our extension
    
    # Time
    # TODO: is this correct? strange that it's a string? Is there a special type?
    # Surprisingly (for such a usual type), time storage is a mess in HDF5.
    # The documentation states that you can use H5T_TIME, but it is 
    # "is not supported. If H5T_TIME is used, the resulting data will be readable
    # and modifiable only on the originating computing platform; it will not be
    # portable to other platforms.". It appears many format are allowed.
    # In addition in h5py, it's indicated as "deprecated" (although it seems
    # it was added in the latest version of HDF5. 
    # Moreover, the only types available are 32 and 64 bits integers as number
    # of seconds since epoch. No past, no milliseconds, no time-zone. 
    # So there are other proposals like in in F5 
    # (http://sciviz.cct.lsu.edu/papers/2007/F5TimeSemantics.pdf) to represent
    # time with a float, a unit and an offset.
    # KNMI uses a string like this: DD-MON-YYYY;HH:MM:SS.sss. 
    # (cf http://www.knmi.nl/~beekhuis/documents/publicdocs/ir2009-01_hdftag36.pdf)
    # So, to not solve anything, we save the date as a string in ISO 8601
    if model.MD_ACQ_DATE in image.metadata:
        ad = datetime.utcfromtimestamp(image.metadata[model.MD_ACQ_DATE])
        adstr = ad.strftime("%Y-%m-%dT%H:%M:%S.%f")
        group["TOffset"] = adstr
        
    # Scale
    if model.MD_PIXEL_SIZE in image.metadata:
        # DimensionScales are not clearly explained in the specification to 
        # understand what they are supposed to represent. Surprisingly, there
        # is no official way to attach a unit.
        pxs = image.metadata[model.MD_PIXEL_SIZE]
        group["DimensionScaleX"] = pxs[0]
        group["DimensionScaleX"].attrs["UNIT"] = "m"
        group["DimensionScaleY"] = pxs[1]
        group["DimensionScaleY"].attrs["UNIT"] = "m" # our extension
        # No clear what's the relation between this name and the label
        dataset.dims.create_scale(group["DimensionScaleX"], "X")
        dataset.dims.create_scale(group["DimensionScaleY"], "Y")
        dataset.dims[l-1].attach_scale(group["DimensionScaleX"])
        dataset.dims[l-2].attach_scale(group["DimensionScaleY"])
        
def _add_image_metadata(group, image):
    """
    Adds the basic metadata information about an image (scale and offset)
    group (HDF Group): the group that will contain the metadata (named "PhysicalData")
    image (DataArray): image with metadata
    """
    if model.MD_DESCRIPTION in image.metadata:
        group["Title"] = image.metadata[model.MD_DESCRIPTION]
    
    if model.MD_IN_WL in image.metadata:
        iwl = image.metadata[model.MD_IN_WL]
        xwl = numpy.mean(iwl) # in m
        group["ExcitationWavelength"] = xwl
    
        # TODO: indicate this is epifluorescense?
    
    if model.MD_OUT_WL in image.metadata:
        owl = image.metadata[model.MD_OUT_WL]
        ewl = numpy.mean(owl) # in m
        group["EmissionWavelength"] = ewl
        
    if model.MD_LENS_MAG in image.metadata:
        group["Magnification"] = image.metadata[model.MD_LENS_MAG]

def _add_acquistion_svi(group, data, **kwargs):
    """
    Adds the acquisition data according to the sub-format by SVI
    group (HDF Group): the group that will contain the metadata (named "PhysicalData")
    image (DataArray): image with metadata (2D image is the lowest dimension)
    """
    gi = group.create_group("ImageData")
    # one of the main thing is that data must always be with 5 dimensions,
    # in this order: CTZYX => so we add dimensions to data if needed
    if len(data.shape) < 5:
        shape5d = [1] * (5-len(data.shape)) + list(data.shape)
        data = data.reshape(shape5d)
    ids = _create_image_dataset(gi, "Image", data, **kwargs)
    _add_image_info(gi, ids, data)
    gp = group.create_group("PhysicalData")
    _add_image_metadata(gp, data)    

def _saveAsHDF5(filename, ldata, thumbnail, compressed=True):
    """
    Saves a list of DataArray as a HDF5 (SVI) file.
    filename (string): name of the file to save
    ldata (list of DataArray): list of 2D data of int or float. Should have at least one array
    thumbnail (None or DataArray): see export
    compressed (boolean): whether the file is compressed or not.
    """
    # TODO check what is the format in Odemis if image is 3D (ex: each pixel has
    # a spectrum associated, or there is a Z axis as well) and convert to CTZYX.
    
    # h5py will extend the current file by default, so we want to make sure
    # there is no file at all.
    try:
        os.remove(filename)
    except OSError:
        pass
    f = h5py.File(filename, "w") # w will fail if file exists 
    if compressed:
        # szip is not free for commercial usage and lzf doesn't seem to be 
        # well supported yet 
        compression = "gzip"
    else:
        compression = None
    
    if thumbnail is not None:
        # Save the image as-is in a special group "Preview"
        prevg = f.create_group("Preview")
        ids = _create_image_dataset(prevg, "Image", thumbnail, compression=compression)
        _add_image_info(prevg, ids, thumbnail)
    
    for i, data in enumerate(ldata):
        g = f.create_group("Acquisition%d" % i)
        _add_acquistion_svi(g, data, compression=compression)
    
    f.close()

def export(filename, data, thumbnail=None):
    '''
    Write a HDF5 file with the given image and metadata
    filename (string): filename of the file to create (including path)
    data (list of model.DataArray, or model.DataArray): the data to export, 
        must be 2D of int or float. Metadata is taken directly from the data 
        object. If it's a list, a multiple page file is created.
    thumbnail (None or model.DataArray): Image used as thumbnail for the file. Can be of any
      (reasonable) size. Must be either 2D array (greyscale) or 3D with last 
      dimension of length 3 (RGB). If the exporter doesn't support it, it will
      be dropped silently.
    '''
    if isinstance(data, list):
        _saveAsHDF5(filename, data, thumbnail)
    else:
        # TODO should probably not enforce it: respect duck typing
        assert(isinstance(data, model.DataArray))
        _saveAsHDF5(filename, [data], thumbnail)
