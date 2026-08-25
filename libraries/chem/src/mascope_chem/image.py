# -*- coding: utf-8 -*-
"""Data visualization related functions and classes.

Created on Fri Mar  6 13:45:15 2020

@author: Oskari Kausiala
"""

import os
from base64 import b64decode, b64encode
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
from multiprocessing import Process

import datashader as ds
import datashader.transfer_functions as tf
import h5py
import numpy as np
import pandas as pd
import xarray
from colorcet import fire
from PIL import Image

from mascope_chem.runtime import runtime


VIZ_TYPES_SUPPORTED = {"spectrogram", "timeseries", "waterfall"}

# JSON compatible template for plotly scatter trace
DEFAULT_TRACE = {
    "x": [],
    "y": [],
    "name": "",
    "mode": "lines",
    "type": "scattergl",
    "line": {"color": "#fff"},
    "visible": True,
    "hoverinfo": "name,y",
}


def convert_to_base64(img):
    """Convert PIL image to base64 png string

    Parameters
    ----------
    img : Image
        Input PIL Image

    Returns
    -------
    str
        Input image as base64 png string

    """

    b = BytesIO()
    img.save(b, format="png")
    return "data:image/png;base64,{0}".format(b64encode(b.getvalue()).decode("utf-8"))


def convert_base64_to_img(b64_img):
    """Convert base64 png string into PIL Image

    Parameters
    ----------
    b64_img : str
        base64 png string

    Returns
    -------
    PIL.Image.Image
        Image object
    """

    str_img = b64decode(b64_img.split(",")[1])
    b = BytesIO(str_img)
    img = Image.open(b)
    return img


def gen_heatmap_image(
    data_xarray,
    t_range=None,
    mz_range=None,
    y_range=None,
    img_width=None,
    img_height=600,
    cmap=fire,
):
    """Generate heatmap image to visualize spectra in 2D using datashader

    # TODO: At the moment ugly workarounds needed to generate 1 px wide heatmap
            slice. Raise an issue in datashader project?

    Parameters
    ----------
    data_xarray : DataArray
        Array with the data to visualize
    t_range : tuple, optional
        2-tuple with start time and end time of the desired time
        range (seconds), by default None. If None, full range will be used.
    mz_range : tuple, optional
        2-tuple with min m/z and max m/z of the desired mass
        range, by default None. If None, full range will be used.
    img_width : int, optional
        Image width (pixels), by default None. If None, data_xarray.shape[1]
        is used.
    img_height : int, optional
        Image height (pixels), by default 600

    Returns
    -------
    Image
        PIL Image containing the heatmap, time advances from
        left to right,  m/z from bottom to top.

    """

    # Ignore RuntimeWarning: "invalid value encountered in double_scalars
    # xres = (xcoords[-1] - xcoords[0]) / (w - 1)"
    # warnings.filterwarnings("ignore", category=RuntimeWarning)
    if data_xarray.time.dtype == "<m8[ns]":
        # Convert timedelta to float
        data_xarray = data_xarray.assign_coords(
            {"time": [float(t.item() * 1e-9) for t in data_xarray.time]}
        )

    t0 = t1 = None
    if not data_xarray.time.shape:
        # Array has no time dimension, need to expand dimensions
        t0 = float(data_xarray.time)
        data_xarray = data_xarray.expand_dims({"time": [t0]}, axis=1)

    if img_width is None:
        img_width = len(data_xarray.time)

    if data_xarray.time.shape[0] == 1:
        # Only one spectrum to visualize, need to extend the array
        t0 = data_xarray.time[0].item()
        # Concatenate the array with itself
        data_xarray = xarray.concat([data_xarray, data_xarray], dim="time")
        t1 = t0 + 1  # Arbitrary t1 to avoid "empty take"

    if t_range is None:
        t_range = (t0 or data_xarray.time[0].item(), t1 or data_xarray.time[-1].item())

    if mz_range is None:
        mz0 = data_xarray.mz[0].item()
        mz1 = data_xarray.mz[-1].item()
        mz_range = (mz0, mz1)

    # Replace values < 0 with nan (not to be visualized, not to blow up the scale)
    data_xarray = data_xarray.where(data_xarray >= 0)
    # Drop rows with all nan to avoid gaps
    data_xarray = data_xarray.dropna(dim="mz", how="all")
    # Check if only zeros in the range
    sig_max = (
        data_xarray.sel(time=slice(*t_range), mz=slice(*mz_range))
        .max()
        .compute()
        .item()
    )
    if sig_max == 0:
        # No need to compute, just return black
        img = Image.new("RGBA", (img_width, img_height), (0, 0, 0))
        return img

    cvs = ds.Canvas(
        x_range=t_range, y_range=mz_range, plot_height=img_height, plot_width=img_width
    )
    agg = cvs.quadmesh(data_xarray, x="time", y="mz")

    if y_range is not None:
        # Bottom left corner pixel to y_range[1] for scaling
        agg[0, 0] = y_range[1]

    img = tf.shade(agg, cmap=cmap)
    # img = tf.set_background(img, "black")
    return img.to_pil()


def gen_ridge_traces(ridge_df, t=None, mz=None):
    """Generate Plotly traces for ridges

    Parameters
    ----------
    ridge_df : DataFrame
        DataFrame containing the ridges (generated by KOnlinePeakId)
    t : list, optional
        Time vector, by default None. If None, indices will be used
        instead of actual time.
    mz : list, optional
        m/z vector, by default None. If None, indices will be used
        instead of actual m/z.
    """

    global DEFAULT_TRACE  # Trace template

    traces = []
    for _, row in ridge_df.iterrows():
        ridge = row.ridge
        if t is not None:
            x = [t[i] for i in ridge[1]]
        else:
            x = ridge[1]
        if mz is not None:
            y = [mz[i] for i in ridge[0]]
        else:
            y = ridge[0]
        ridge_trace = deepcopy(DEFAULT_TRACE)
        ridge_trace.update({"x": x, "y": y, "name": row.name})
        traces.append(ridge_trace)
    return traces


def gen_spec_stack_image(
    data_xarray, t_range, mz_range, avg_s=0.0, img_width=600, img_height=1200
):
    """Generate spec images and stack them

    Parameters
    ----------
    data_xarray : DataArray
        Array with the data to visualize
    t_range : tuple, optional
        2-tuple with start time and end time of the desired time
        range (seconds), by default None. If None, full range will be used.
    mz_range : tuple, optional
        2-tuple with min m/z and max m/z of the desired mass
        range, by default None. If None, full range will be used.
    avg_s : float, optional
        Length of data to be averaged per trace (seconds), by default 0.0.
        If <= data time resolution, one trace per spectrum will be generated.
    img_width : int, optional
        Image width (pixels), by default 600
    img_height : int, optional
        Image height (pixels), by default 1200

    Returns
    -------
    Image
        PIL Image containing the spec stack.

    """

    # Select subset of data within set ranges
    sub_xarray = data_xarray.sel(
        mz=slice(mz_range[0], mz_range[1]), time=slice(t_range[0], t_range[1])
    )

    # Convert time dimension to timedelta for resampling
    sub_xarray = sub_xarray.assign_coords(
        {"time": [timedelta(seconds=t.item()) for t in sub_xarray.time]}
    )

    # Check aceraging parameter
    if avg_s <= 0:
        # Set avg_s to data time resolution. The coord's resolution is not
        # fixed - pandas 2 forces nanoseconds while pandas 3 keeps the
        # microseconds it was built from - so .item() hands back an int of ns
        # on one and a datetime.timedelta on the other. Timedelta takes both.
        avg_s = pd.Timedelta(
            (sub_xarray.time[1] - sub_xarray.time[0]).item()
        ).total_seconds()

    # Resample to 'avg_s'. The offset alias must be lowercase: pandas 3 removed
    # the uppercase "S" and raises on it rather than rewriting it.
    sub_xarray = sub_xarray.resample(time="%.1fs" % avg_s).mean()

    # Generate trace per spectrum
    traces = []
    for spectrum in sub_xarray.transpose():
        imgi = gen_spec_image(
            spectrum, y_range=None, img_width=img_width, img_height=200
        )
        t = pd.Timedelta(spectrum.time.data.item()).total_seconds()
        traces.append({"t_range": [t, t], "img": imgi})

    # Stack images
    img = stack_spec_images(
        traces, t_range=None, n_traces=None, img_width=img_width, img_height=img_height
    )
    return img


def gen_spec_image(
    data_xarray,
    mz_range=None,
    y_range=None,
    img_width=600,
    img_height=200,
    cmap="white",
):
    """Function to generate single spec trace image using datashader.
    Signals in the input array will be flattened time-wise.

    TODO: Something need to be done
    with y scaling?

    Parameters
    ----------
    data_xarray : DataArray
        Array with the data to visualize
    y_range : tuple, optional
        2-tuple with signal range to be visualized, by default None.
        If None, range will be from 0 to max(signal).
    img_width : int, optional
        Image width (pixels), by default 600
    img_height : int, optional
        Image height (pixels), by default 200

    Returns
    -------
    Image
        PIL Image containing a trace for a single spectrum.

    """

    # Flatten
    if "time" in data_xarray.dims:
        # Sum timewise to get y
        y = data_xarray.mean(dim="time")
    else:
        y = data_xarray.values

    if mz_range is None:
        mz0 = data_xarray.mz[0].item()
        mz1 = data_xarray.mz[-1].item()
        mz_range = (mz0, mz1)

    if y_range is None:
        # Set y_range[1] to max of signal
        y_max = float(y.max())
        y_range = (0, y_max)
    # Make sure yrange[1] not zero
    y1 = max(1e-5, y_range[1])
    y_range = (y_range[0], y1)

    # mz axis
    mz = data_xarray.mz.data
    # Generate image for single trace
    cvs = ds.Canvas(
        x_range=mz_range, y_range=y_range, plot_height=img_height, plot_width=img_width
    )
    data_df = pd.DataFrame({"mz": mz, "y": y})
    # Drop rows with all nan to avoid gaps
    data_df.dropna(axis=0, how="all", subset=["y"], inplace=True)
    agg = cvs.line(data_df, "mz", "y")
    img = tf.shade(agg, cmap=cmap)
    return img.to_pil()


def gen_timeseries_trace(data_xarray, t_range=None, mz_range=None, y_range=None):
    """Generate timeseries trace for certain time and mz ranges.
    All signals in the said mz range will be summed to make one trace.


    Parameters
    ----------
    data_xarray : DataArray
        Array with the data to visualize
    t_range : tuple, optional
        2-tuple with start time and end time of the desired time
        range (seconds), by default None. If None, full range will be used.
    mz_range : tuple, optional
        2-tuple with min m/z and max m/z of the desired mass
        range, by default None. If None, full range will be used.
    y_range : tuple, optional
        Not used, only for unified API with other functions

    Returns
    -------
    dict
        JSON compatible plotly scatter trace where x = time
        and y = signal

    """

    if t_range is None:
        t_range = (data_xarray.time[0], data_xarray.time[-1])
    if mz_range is None:
        mz_range = (data_xarray.mz[0], data_xarray.mz[-1])
    sub_xarray = data_xarray.sel(
        mz=slice(mz_range[0], mz_range[1]), time=slice(t_range[0], t_range[1])
    )
    trace = deepcopy(DEFAULT_TRACE)
    trace.update(
        {
            "x": np.array(sub_xarray.time).tolist(),
            "y": np.array(sub_xarray.sum(dim="mz")).tolist(),
        }
    )
    return trace


def hstack_imgs(slice_imgs):
    """Merge image slices horizontally into one image

    Assumes all images to be merged are of same height

    Parameters
    ----------
    slice_imgs : list
        List of slice Images to merge

    Returns
    -------
    PIL.Image.Image
        merged image

    """

    # Calculate image width
    img_width = 0
    img_height = 0
    for img in slice_imgs:
        img_width += img.size[0]
        # Assume all slices have same height
        img_height = img.size[1]
    merge_img = Image.new("RGBA", (img_width, img_height))
    x = 0
    for img in slice_imgs:
        # Paste slice
        merge_img.paste(img, (x, 0), img)
        x += img.size[0]
    return merge_img


def read_img_from_h5(filename, location):
    """Read image from h5 file and return as PIL.Image.Image

    Parameters
    ----------
    filename : str
        Full file path to read from
    location : str
        Dataset within the file to read the image from

    Returns
    -------
    Image
        PIL Image read from the file

    """

    with h5py.File(filename, "r") as h5f:
        # Read image array from the file
        img_arr = h5f[location][()]
        # Convert to PIL.Image.Image and return
        return Image.fromarray(img_arr)


def stack_spec_images(
    spec_traces, t_range=None, n_traces=None, img_width=600, img_height=1200
):
    """Function to combine list of spec traces into a stack image.

    Horizontal axis is m/z, vertical axis is time.

    NOTE: Currently time axis is reversed!

    TODO: Need to figure out a way to make vertical axis somehow meaningful,
    maybe add some input parameters to enable more control

    Parameters
    ----------
    spec_traces : list
        List of dicts of spec traces. Each dict should be of the form:
        {'img': Image,  't_range': tuple}
    t_range : tuple, optional
        2-tuple with start time and end time of the desired time
        range (seconds), by default None. If None, all traces will be used.
    n_traces : int, optional
        Number of traces to divide the image canvas for, by default None.
        If None, inferred from the number of spec traces.
    img_width : int, optional
        Image width (pixels), by default 600
    img_height : int, optional
        Image height (pixels), by default 1200

    Returns
    -------
    Image
        PIL Image containing the spec stack.

    """

    # Initialize image
    stack_img = Image.new("RGBA", (img_width, img_height))
    # Number of images to combine
    n_imgs = len(spec_traces)
    # Number of traces to make room for
    if n_traces is None:
        n_traces = n_imgs
    # No images
    if n_imgs == 0:
        return stack_img
    # Height of a single image
    spec_img_height = spec_traces[0].get("img").size[1]
    # Vertical offset between images to be stacked
    if n_traces == 1:
        offset = img_height - spec_img_height
        y_pos = offset
    else:
        offset = (img_height - spec_img_height) / (n_traces - 1)
        y_pos = img_height - spec_img_height
    # Set indices in case there are more images
    if t_range is None:
        # No time range specified
        i0 = 0
        i1 = i0 + min(n_traces, n_imgs)
    else:
        # Select traces in the requested time range
        i0 = None
        i1 = None
        for i, trace in enumerate(spec_traces):
            if i0 is None and trace.get("t_range")[0] >= t_range[0]:
                i0 = i
            elif trace.get("t_range")[1] > t_range[1]:
                i1 = i
                break
        if i1 is None:
            i1 = len(spec_traces)
    # Loop through images in reversed order
    # to make newest one appear on the bottom
    for trace in list(reversed(spec_traces))[i0:i1]:
        img = trace.get("img")
        stack_img.paste(img, (0, int(y_pos)), img)
        y_pos -= offset
    return stack_img


def write_img_to_h5(filename, location, img):
    """Write an image to h5 file as an array of uint8

    Parameters
    ----------
    filename : str
        Full file path to write into
    location : str
        Dataset within the file to write the image into
    img : Image
        PIL Image to write to file

    """

    runtime.logger.info("Writing image to : " + location)
    with h5py.File(filename, "r+") as h5f:
        if location in h5f:
            # Delete previous image if exists
            del h5f[location]
        # Write image as an array to file
        img_arr = np.array(img)
        h5f.create_dataset(location, data=img_arr)
        # h5f.create_dataset(location,
        #                    np.shape(img),
        #                    h5py.h5t.STD_U8BE,
        #                    data=img
        #                    )


VIZ_GENERATORS = {
    "spectrogram": gen_heatmap_image,
    "timeseries": gen_timeseries_trace,
    "waterfall": gen_spec_image,
}


class ImageGenerator(Process):
    def __init__(self, queue_in, queue_out, shutdown_event):
        Process.__init__(self)
        self.queue_in = queue_in
        self.queue_out = queue_out
        self.shutdown_event = shutdown_event
        # Per-process guard: the generation loop can fail on every queued
        # frame (e.g. after acquisition stop), so only the first failure is
        # logged with its traceback at ERROR - repeats go to DEBUG.
        self._generation_error_logged = False

    def run(self):
        global VIZ_GENERATORS
        runtime.logger.info(f"ImageGenerator started - PID: {os.getpid()}")
        while not self.shutdown_event.is_set():
            try:
                data = self.queue_in.get()
            except KeyboardInterrupt:
                # Normal shutdown of the generator process, not a fault
                runtime.logger.info(f"KeyboardInterrupt for PID: {os.getpid()}")
                break
            except Exception:
                runtime.logger.exception(
                    f"Failed to read from image queue for PID: {os.getpid()}"
                )
                break
            if data is not None:
                # Select function to generate the image
                viz_type = data["viz_type"]
                try:
                    viz_gen_func = VIZ_GENERATORS[viz_type]
                except KeyError:
                    # WARNING: repeats per queued frame while the mismatch lasts
                    runtime.logger.warning(
                        f"Requested visualization type '{viz_type}' not available!"
                    )
                    continue
                data_array = data.pop("data")
                mz_range = data.get("mz_range", None)
                y_range = data.get("y_range", None)
                try:
                    viz = viz_gen_func(data_array, mz_range=mz_range, y_range=y_range)
                except ZeroDivisionError:
                    # Routine for degenerate frames (e.g. empty y-range)
                    runtime.logger.debug(
                        f"Caught ZeroDivisionError in {str(viz_gen_func)}"
                    )
                    continue
                except Exception as e:
                    # TODO: check if this exception handling is right: without it process hangs
                    # after acq.stopped, often there goes exception: y must be real (y_range-[0, 15.135354995727539])
                    if not self._generation_error_logged:
                        self._generation_error_logged = True
                        runtime.logger.exception(
                            f"ImageGenerator {os.getpid()} exception for y_range {y_range}"
                        )
                    else:
                        runtime.logger.debug(
                            f"ImageGenerator {os.getpid()} exception: {str(e)} for y_range {y_range}"
                        )
                    continue
                if isinstance(viz, Image.Image):
                    img_b = convert_to_base64(viz)
                    data.update({"img": img_b})
                elif isinstance(viz, dict):
                    data.update({"traces": [viz]})
                self.queue_out.put(data)
            else:
                runtime.logger.info(f"ImageGenerator stopped - PID: {os.getpid()}")
                break
