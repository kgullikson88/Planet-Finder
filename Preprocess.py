import sys
import os

import FittingUtilities
from astropy.io import fits
from astropy import units, constants
import numpy as np
import matplotlib.pyplot as plt

import HelperFunctions
import astropy.time as time
import GenericSearch
from numpy.polynomial import chebyshev
from scipy.interpolate import InterpolatedUnivariateSpline as spline
from scipy.optimize import leastsq



def ReadFile(fname, blaze=None):
    try:
        orders = HelperFunctions.ReadFits(fname)
    except ValueError:
        orders = HelperFunctions.ReadFits(fname, errors=2)
    orders = orders[::-1]  # Reverse order so the bluest order is first

    #Need to blaze-correct the later data
    if int(fname[2:7]) > 50400 and blaze is not None:
        print "\tBlaze correcting!"
        try:
            blaze_orders = HelperFunctions.ReadFits(blaze)
        except ValueError:
            blaze_orders = HelperFunctions.ReadFits(blaze, errors=2)
        blaze_orders = blaze_orders[::-1]  # Reverse order so the bluest order is first
        blazecorrect = True
    else:
        blazecorrect = False

    for i, order in enumerate(orders):
        if blazecorrect:
            b = blaze_orders[i].y/blaze_orders[i].y.mean()
            #plt.plot(order.x, b, 'g-', alpha=0.4)
            order.y /= b
        order.cont = FittingUtilities.Continuum(order.x, order.y, fitorder=2, lowreject=2, highreject=5)
        #plt.plot(order.x, order.y, 'k-', alpha=0.4)
        #plt.plot(order.x, order.cont, 'r-', alpha=0.4)
        orders[i] = order.copy()
    #plt.show()
    return orders





def OutputFile(orders, fname, outfilename):
    print "Outputting to {}".format(outfilename)
    column_list = []
    for i, order in enumerate(orders):
        #order.cont = FittingUtilities.Continuum(order.x, order.y, fitorder=2, lowreject=1.5, highreject=5)
        columns = columns = {"wavelength": order.x,
                             "flux": order.y,
                             "continuum": order.cont,
                             "error": order.err}
        column_list.append(columns)
    HelperFunctions.OutputFitsFileExtensions(column_list, fname, outfilename, mode="new")
    return


      

def SortFiles():
    allfiles = [f for f in os.listdir("./") if f.startswith("RV") and f.endswith(".fits") and "-" not in f and "smoothed" not in f]
    Blaze = ["Blazefiles/{}".format(f) for f in os.listdir("Blazefiles")]
    object_files = []
    I2_files = []
    Blaze_files = []

    # First, figure out which files are I2 vs object files
    for fname in allfiles:
        header = fits.getheader(fname)
        time_obs = header['date-obs'] + "T" + header['UT']
        t = time.Time(time_obs, format='isot', scale='utc').jd
        if "psi" in header['object'].lower():
            # Object file
            object_files.append((fname, t))
        else:
            I2_files.append((fname, t))

    # Get the time for each of the blaze files
    for fname in Blaze:
        header = fits.getheader(fname)
        time_obs = header['date-obs'] + "T" + header['UT']
        t = time.Time(time_obs, format='isot', scale='utc').jd
        Blaze_files.append((fname, t))

    # Read in the RV data
    bjd, rv = np.loadtxt("psi1draa_100_120_mcomb1.dat", usecols=(0,1), unpack=True)

    # Now, associate an I2/blaze file and rv shift with each object
    association = {}
    for fname, jd in object_files:
        # Associate with I2
        bestdiff = np.inf
        bestidx = 0
        for i, (I2file, I2time) in enumerate(I2_files):
            if abs(I2time - jd) < bestdiff:
                bestdiff = abs(I2time - jd)
                bestidx = i
        bestI2 = I2_files[bestidx][0]

        # Associate with blaze
        bestdiff = np.inf
        bestidx = 0
        for i, (Blazefile, Blazetime) in enumerate(Blaze_files):
            if abs(Blazetime - jd) < bestdiff:
                bestdiff = abs(Blazetime - jd)
                bestidx = i
        bestblaze = Blaze_files[bestidx][0]

        # Associate with rv
        #idx = np.argmin(abs(bjd - jd))
        #vel = rv[idx]
        #vel = GenericSearch.HelCorr(header, observatory="McDonald")*1000.0
        vel = 0.0

        association[fname] = [bestI2, bestblaze, vel]


    return association


def poly(pars, m, l, h, x):
    xgrid = (x - m) / (h - l)
    return chebyshev.chebval(xgrid, pars)


def i2_errfcn(pars, data, i2, maxdiff=0.05):
    dx = poly(pars, np.median(data.x), min(data.x), max(data.x), data.x)
    penalty = np.sum(np.abs(dx[np.abs(dx) > maxdiff]))
    retval = (data.y/data.cont - i2(data.x + dx)) + penalty
    return retval

def fit_i2(order, i2, fitorder=1):
    i2_fcn = spline(i2.x, i2.y/i2.cont)
    pars = np.zeros(fitorder + 1)
    args = (order, i2_fcn, 0.05)
    output = leastsq(i2_errfcn, pars, args=args, full_output=True, xtol=1e-12, ftol=1e-12)
    pars = output[0]

    new_i2 = order.copy()
    dx = poly(pars, np.median(order.x), min(order.x), max(order.x), order.x)
    new_i2.y = i2_fcn(order.x + dx)
    new_i2.cont = np.ones(new_i2.size())
    return new_i2


def DoAll():
    c = constants.c.cgs.to(units.m/units.s)
    association = SortFiles()

    for object_file in association.keys():
        print object_file
        outputfile = "{}-1.fits".format(object_file[:-5])
        object_orders = ReadFile(object_file, blaze=association[object_file][1])
        I2_orders = ReadFile(association[object_file][0], blaze=association[object_file][1])

        corrected_orders = []

        for o, i2 in zip(object_orders, I2_orders):
            if "68257" not in object_file and o.x[-1] > 500 and o.x[0] < 640:
                new_i2 = fit_i2(o.copy(), i2.copy())

                o.y /= new_i2.y/new_i2.cont
            o.x /= (1.0 + association[object_file][2] / c.value)
            corrected_orders.append(o.copy())
            #plt.plot(o.x, o.y/o.cont, 'k-', alpha=0.4)
            #plt.plot(o.x, i2.y/i2.cont, 'r-', alpha=0.4)
            new_i2 = fit_i2(o.copy(), i2.copy())
            #plt.plot(o.x, new_i2.y/new_i2.cont, 'g-', alpha=0.4)
        #plt.show()
        #sys.exit()
        OutputFile(corrected_orders, object_file, outputfile)




if __name__ == "__main__":
    DoAll()
    for fname in sys.argv[1:]:
        header = fits.getheader(fname)
        if  "psi" in header['object'].lower():
            print fname
            ReadFile(fname)