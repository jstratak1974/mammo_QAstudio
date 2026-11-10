"""COQ-derived numerical primitives for Mammo QA Studio.

Adapted from the independent COQ Python reimplementation previously built from
the COQ manual and compiled JAR. Only the routines used by this app are included.
Coordinates are zero based; ROI = (x, y, width, height), spacing = (dy, dx) mm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage, optimize

MODELS = ("Identity", "Linear", "Log(1+cK)", "Log(K)", "Square root")


def positive(value, name):
    value = float(value)
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero.")
    return value


def image2d(image):
    a = np.asarray(image, dtype=float)
    if a.ndim != 2 or min(a.shape) < 2 or not np.isfinite(a).all():
        raise ValueError("A finite, grayscale 2D image of at least 2 x 2 pixels is required.")
    return a


def spacing2d(spacing):
    if np.isscalar(spacing):
        spacing = (spacing, spacing)
    return positive(spacing[0], "Row spacing"), positive(spacing[1], "Column spacing")


def crop(image, roi=None):
    a = image2d(image)
    if roi is None:
        return a
    x, y, w, h = map(int, roi)
    if min(x, y) < 0 or min(w, h) < 2 or x+w > a.shape[1] or y+h > a.shape[0]:
        raise ValueError(f"ROI {roi} is outside image bounds {a.shape[1]} x {a.shape[0]}, or smaller than 2 x 2.")
    return a[y:y+h, x:x+w]


@dataclass
class ImageRecord:
    name: str
    pixels: np.ndarray
    spacing: tuple | None
    metadata: dict
    source: str = ""


def _value(ds, key):
    v = getattr(ds, key, None)
    if v is None:
        return None
    if isinstance(v, (list, tuple)) or type(v).__name__ == "MultiValue":
        return [str(x) for x in v]
    return str(v)


@dataclass
class Response:
    model: str = "Identity"
    a: float = 0.
    b: float = 1.
    c: float = 1.
    r2: float | None = None
    dose_min: float | None = None
    dose_max: float | None = None

    def forward(self, dose):
        x = np.asarray(dose, float)
        if self.model == "Identity": return x
        if self.model == "Linear": return self.a+self.b*x
        if self.model == "Log(1+cK)": return self.a+self.b*np.log1p(self.c*x)
        if self.model == "Log(K)": return self.a+self.b*np.log(x)
        if self.model == "Square root": return self.a+self.b*np.sqrt(x)
        raise ValueError("Unknown response model.")

    def inverse(self, signal):
        x = np.asarray(signal, float)
        if self.model == "Identity": return x.copy()
        if self.model not in MODELS or not all(np.isfinite(v) for v in (self.a, self.b, self.c)) or abs(self.b) < 1e-15:
            raise ValueError("Invalid response coefficients or zero response slope.")
        with np.errstate(over="ignore", invalid="ignore"):
            z = (x-self.a)/self.b
            if self.model == "Linear": y = z
            elif self.model == "Log(1+cK)": y = np.expm1(z)/positive(self.c, "c")
            elif self.model == "Log(K)": y = np.exp(z)
            elif self.model == "Square root":
                if np.any(z < 0):
                    raise ValueError("Square-root inversion is ambiguous below its response offset; check coefficients and dark correction.")
                y = z*z
        if not np.isfinite(y).all():
            raise ValueError("Linearization produced non-finite values. Check response coefficients and input range.")
        return y


def fit_response(doses, signals, model="Linear"):
    x, y = np.asarray(doses, float), np.asarray(signals, float)
    if x.ndim != 1 or y.shape != x.shape or len(x) < 4 or not np.isfinite(y).all():
        raise ValueError("Provide at least four finite dose/signal pairs.")
    if np.any(~np.isfinite(x)) or np.any(x <= 0) or len(np.unique(x)) < (4 if model == "Log(1+cK)" else 3):
        raise ValueError("Positive, sufficiently distinct air-kerma measurements are required.")
    if model not in MODELS[1:]:
        raise ValueError("Select a fitting model other than Identity.")
    if model == "Log(1+cK)":
        def fun(v, a, b, c): return a+b*np.log1p(c*v)
        p, _ = optimize.curve_fit(fun, x, y, p0=(float(y.min()), float(np.ptp(y)), 1/float(x.mean())),
                                  bounds=([-np.inf, -np.inf, 1e-10], [np.inf, np.inf, np.inf]), maxfev=50000)
        response = Response(model, *map(float, p))
    else:
        u = x if model == "Linear" else np.log(x) if model == "Log(K)" else np.sqrt(x)
        b, a = np.polyfit(u, y, 1)
        response = Response(model, float(a), float(b))
    residual = y-response.forward(x)
    sst = float(np.sum((y-y.mean())**2))
    if sst <= 0 or abs(response.b) < 1e-15:
        raise ValueError("No measurable response variation; a valid inverse cannot be fitted.")
    response.r2 = float(1-np.sum(residual**2)/sst)
    response.dose_min, response.dose_max = float(x.min()), float(x.max())
    return response


def mtf_crossing(frequency, mtf, level):
    f, m = np.asarray(frequency), np.asarray(mtf)
    indexes = np.flatnonzero((m[:-1] >= level) & (m[1:] < level))
    if not len(indexes): return None
    i = indexes[0]
    return float(f[i]+(level-m[i])*(f[i+1]-f[i])/(m[i+1]-m[i]))


def _edge_geometry(a, spacing, orientation):
    if orientation == "auto":
        gx = np.mean(np.abs(np.diff(a, axis=1)))
        gy = np.mean(np.abs(np.diff(a, axis=0)))
        orientation = "vertical" if gx >= gy else "horizontal"
    if orientation not in ("vertical", "horizontal"):
        raise ValueError("Orientation must be auto, vertical, or horizontal.")
    if orientation == "horizontal":
        a, spacing = a.T, spacing[::-1]
    return a, spacing, orientation


def _trace_line(a, slit=False):
    h, w = a.shape
    if min(h, w) < 24:
        raise ValueError("Use an edge/slit ROI at least 24 pixels in both dimensions.")
    smooth = ndimage.gaussian_filter(a, (0.5, 1.))
    if slit:
        baseline = (np.median(a[:, :max(2,w//10)],axis=1)+np.median(a[:, -max(2,w//10):],axis=1))/2
        z = smooth-baseline[:,None]
        if abs(np.percentile(z, 1)) > abs(np.percentile(z, 99)): z = -z
    else:
        z = np.abs(np.gradient(smooth, axis=1))
    pos = z[:, 3:-3].argmax(axis=1)+3
    strengths, locations = [], []
    for row, p in zip(z, pos):
        vals = np.maximum(row[max(0,p-3):p+4],0)
        coords = np.arange(max(0,p-3),p+4)
        strengths.append(vals.sum())
        locations.append(float(np.sum(vals*coords)/max(vals.sum(),1e-30)))
    loc = np.asarray(locations); strengths = np.asarray(strengths)
    valid = strengths > np.median(strengths)*0.25
    yy = np.arange(h,dtype=float)
    for _ in range(3):
        if valid.sum() < max(12,h//2):
            raise ValueError("Edge/slit could not be reliably traced. Tighten the ROI around a single straight feature.")
        slope, intercept = np.polyfit(yy[valid],loc[valid],1)
        residual = loc-(slope*yy+intercept)
        mad = np.median(np.abs(residual[valid]))
        valid &= np.abs(residual) <= max(0.5,4*mad)
    return float(slope), float(intercept), float(np.sqrt(np.mean(residual[valid]**2))), int(valid.sum())


def mtf(image, spacing, roi=None, method="edge", orientation="auto", oversampling=10,
        profile_mm=10., derivative_correction=True, slit_width_mm=0.):
    """Distance-binned presampled edge/slit MTF; returns only frequencies <= Nyquist."""
    a, sp, orientation = _edge_geometry(crop(image,roi), spacing2d(spacing), orientation)
    dy, dx = sp
    if method not in ("edge", "slit") or not 2 <= int(oversampling) <= 20:
        raise ValueError("Select edge/slit and oversampling between 2 and 20.")
    slope, intercept, fit_rmse, used_rows = _trace_line(a, slit=method=="slit")
    physical_slope = slope*dx/dy
    angle = math.degrees(math.atan(physical_slope))
    if abs(angle) < 0.5 or abs(angle) > 15:
        raise ValueError(f"Feature angle is {angle:.3f} degrees. Use a straight slanted edge/slit (approximately 1-5 degrees; supported 0.5-15).")
    xx, yy = np.meshgrid(np.arange(a.shape[1]),np.arange(a.shape[0]))
    normalizer = math.sqrt(1+physical_slope**2)
    distances = (xx-intercept-slope*yy)*dx/normalizer
    # Restrict to a profile present in every row; do not extrapolate missing edge tails.
    span = min(positive(profile_mm,"Profile length")/2, -float(distances[:,0].max()), float(distances[:,-1].min()))
    step = dx/int(oversampling)
    n = int(2*span/step)
    if n < 100:
        raise ValueError("Not enough common edge/slit profile. Widen ROI or reduce oversampling.")
    edges = np.linspace(-span,span,n+1)
    counts,_ = np.histogram(distances.ravel(),edges)
    sums,_ = np.histogram(distances.ravel(),edges,weights=a.ravel())
    centers = (edges[:-1]+edges[1:])/2
    good = counts > 0
    missing_fraction = float((~good).mean())
    if missing_fraction > 0.1:
        raise ValueError("More than 10% of subpixel bins are empty. Increase feature length or reduce oversampling.")
    profile = np.interp(centers,centers[good],sums[good]/counts[good])
    step = float(centers[1]-centers[0])
    if method == "edge":
        low, high = np.mean(profile[:max(3,n//10)]),np.mean(profile[-max(3,n//10):])
        if abs(high-low) < 1e-12*max(1,abs(high),abs(low)):
            raise ValueError("No adequate edge contrast detected.")
        profile = (profile-low)/(high-low)
        lsf = np.diff(profile)/step
        lsf_x = (centers[:-1]+centers[1:])/2
    else:
        profile = profile-(np.mean(profile[:max(3,n//10)])+np.mean(profile[-max(3,n//10):]))/2
        if abs(profile.min()) > abs(profile.max()): profile = -profile
        lsf, lsf_x = profile.copy(),centers
    window = np.hamming(len(lsf))
    transform = np.abs(np.fft.rfft(lsf*window))
    if transform[0] < 1e-15:
        raise ValueError("Zero LSF integral; check the selected feature.")
    spectrum = transform/transform[0]
    f = np.fft.rfftfreq(len(lsf),step)
    if method == "edge" and derivative_correction:
        spectrum /= np.sinc(f*step)  # first-difference transfer function
    if method == "slit" and slit_width_mm:
        aperture = np.sinc(f*positive(slit_width_mm,"Slit width"))
        if np.any(aperture[f <= .5/dx] < .2):
            raise ValueError("Slit-width correction approaches its first zero; use a narrower slit.")
        spectrum /= aperture
    valid = f <= .5/dx
    f,spectrum = f[valid],spectrum[valid]
    return {"frequency":f,"mtf":spectrum,"profile_x_mm":centers,"profile":profile,
            "lsf_x_mm":lsf_x,"lsf":lsf,"method":method,"orientation":orientation,
            "angle_deg":angle,"trace_rmse_px":fit_rmse,"trace_rows":used_rows,
            "empty_bin_fraction":missing_fraction,"nyquist_lp_mm":.5/dx,
            "mtf50_lp_mm":mtf_crossing(f,spectrum,.5),"mtf10_lp_mm":mtf_crossing(f,spectrum,.1),
            "oversampling":int(oversampling),"actual_profile_mm":2*span,
            "derivative_correction":bool(derivative_correction),"slit_width_mm":float(slit_width_mm)}


def nps(images, spacing, roi=None, fragment=256, overlap=0.5, detrend="mean", window="none",
        omit=1, lines=7, reject_coq=True):
    """Area-normalized two-sided NPS (signal^2 mm^2) and NNPS (mm^2)."""
    dy,dx = spacing2d(spacing)
    if not np.isclose(dx,dy,rtol=1e-5):
        raise ValueError("1D NNPS/DQE extraction currently requires square pixels. MTF and QC support anisotropic pixels.")
    fragment,omit,lines = int(fragment),int(omit),int(lines)
    if fragment < 16 or fragment % 2 or not 0 <= overlap < 1 or omit < 0 or lines < 1 or omit+lines > fragment//2:
        raise ValueError("Use an even fragment >=16, 0<=overlap<1, and axis bands inside the fragment.")
    if detrend not in ("mean","plane","quadratic") or window not in ("none","hann"):
        raise ValueError("Unsupported detrending/window method.")
    arrays = [crop(im,roi) for im in images]
    if not arrays or any(a.shape != arrays[0].shape for a in arrays):
        raise ValueError("NPS images/ROIs must have matching geometry.")
    if min(arrays[0].shape) < fragment:
        raise ValueError("NPS fragment does not fit in the ROI.")
    step = max(1,int(fragment*(1-overlap)))
    ys = range(0,arrays[0].shape[0]-fragment+1,step)
    xs = range(0,arrays[0].shape[1]-fragment+1,step)
    win = np.ones((fragment,fragment)) if window == "none" else np.outer(np.hanning(fragment),np.hanning(fragment))
    win_power = float(np.mean(win**2))
    ny,nx = np.meshgrid(np.linspace(-1,1,fragment),np.linspace(-1,1,fragment),indexing="ij")
    design = np.stack([np.ones_like(nx),nx,ny]+([nx*nx,nx*ny,ny*ny] if detrend=="quadratic" else []),axis=-1).reshape(-1,6 if detrend=="quadratic" else 3)
    projector = np.linalg.pinv(design) if detrend != "mean" else None
    power = np.zeros((fragment,fragment)); norm_power = power.copy()
    accepted = rejected = 0; means = []
    for a in arrays:
        for y in ys:
            for x in xs:
                tile = a[y:y+fragment,x:x+fragment]
                mean = float(tile.mean())
                if abs(mean) < 1e-15: raise ValueError("NNPS cannot be normalized by a zero signal mean.")
                if reject_coq and float(np.ptp(tile)) >= abs(mean):
                    rejected += 1
                    continue
                trend = mean if detrend=="mean" else (design@(projector@tile.ravel())).reshape(tile.shape)
                fft = np.fft.fftshift(np.fft.fft2((tile-trend)*win))
                p = dy*dx/(fragment*fragment*win_power)*abs(fft)**2
                power += p; norm_power += p/(mean*mean)
                means.append(mean); accepted += 1
    if not accepted:
        raise ValueError("All NPS fragments were rejected. Check the flat field, linearization, ROI, or rejection setting.")
    power /= accepted; norm_power /= accepted
    faxis = np.fft.fftshift(np.fft.fftfreq(fragment,dx)); center = fragment//2
    pos = np.arange(center+1,fragment); offsets = np.arange(omit,omit+lines)
    band = np.concatenate([center+offsets,center-offsets])
    # Both opposite frequency signs, and both sides of the selected axis.
    neg = center-(pos-center)
    horizontal = (norm_power[np.ix_(band,pos)].mean(0)+norm_power[np.ix_(band,neg)].mean(0))/2
    vertical = (norm_power[np.ix_(pos,band)].mean(1)+norm_power[np.ix_(neg,band)].mean(1))/2
    yy,xx = np.indices(norm_power.shape)
    radius = np.floor(np.hypot(xx-center,yy-center)).astype(int)
    mask = (abs(xx-center)>=omit)&(abs(yy-center)>=omit)&(radius<center)
    sums = np.bincount(radius[mask],weights=norm_power[mask],minlength=center)
    counts = np.bincount(radius[mask],minlength=center)
    radial = np.divide(sums,counts,out=np.full(center,np.nan),where=counts>0)[1:center]
    return {"frequency":faxis[pos],"horizontal":horizontal,"vertical":vertical,
            "average":(horizontal+vertical)/2,"radial":radial,"nps2d":power,"nnps2d":norm_power,
            "frequency_axis":faxis,"accepted_fragments":accepted,"rejected_fragments":rejected,
            "mean_signal":float(np.mean(means)),"fragment":fragment,"overlap":overlap,
            "detrend":detrend,"window":window,"omit_lines":omit,"band_lines":lines,
            "pixel_spacing_mm":[dy,dx],"reject_coq":bool(reject_coq),
            "legacy_coq_raw_fft_to_mm2_factor":dy*dx/(2*fragment*fragment),
            "legacy_comparison_note":"For mean detrending and no window, multiply the supplied JAR's raw NNPS spectrum by pixel area/(2*N^2) to compare with area-normalized NNPS."}


def validate_curve(frequency, values, name):
    f,v = np.asarray(frequency,float),np.asarray(values,float)
    if f.ndim != 1 or v.shape != f.shape or len(f)<2 or not np.isfinite(f).all() or not np.isfinite(v).all() or np.any(f<0) or np.any(np.diff(f)<=0):
        raise ValueError(f"{name}: require finite, increasing, distinct nonnegative frequencies and values.")
    return f,v


def dqe(mtf_frequency, mtf_values, nps_frequency, nnps_values, kerma_uGy, q_per_uGy, interpolation="linear"):
    mf,mv = validate_curve(mtf_frequency,mtf_values,"MTF")
    nf,nv = validate_curve(nps_frequency,nnps_values,"NNPS")
    if np.any(mv<0) or np.any(nv<=0): raise ValueError("MTF must be nonnegative and NNPS strictly positive.")
    q = positive(kerma_uGy,"Detector air kerma")*positive(q_per_uGy,"q per microGy")
    in_range = (nf>=mf.min())&(nf<=mf.max())
    if in_range.sum()<2: raise ValueError("MTF and NNPS frequency ranges do not overlap sufficiently.")
    f,n = nf[in_range],nv[in_range]
    if interpolation == "linear": m = np.interp(f,mf,mv)
    elif interpolation == "COQ quartic":
        if len(mf)<5: raise ValueError("COQ quartic interpolation requires at least five MTF samples.")
        m = np.polynomial.Polynomial.fit(mf,mv,4)(f)
        if np.any(m<0): raise ValueError("Quartic fit has negative MTF values; choose linear interpolation.")
    else: raise ValueError("Unknown DQE interpolation.")
    values = m*m/(n*q)
    return {"frequency":f,"dqe":values,"interpolated_mtf":m,"nnps":n,"q":q,
            "kerma_uGy":float(kerma_uGy),"q_per_uGy":float(q_per_uGy),"interpolation":interpolation,
            "points_outside_overlap":int((~in_range).sum()),"values_above_one":int((values>1).sum())}


def bad_pixels(image, spacing, roi=None, tile_mm=10., threshold_percent=20., margin_mm=15.):
    a = crop(image,roi); dy,dx = spacing2d(spacing)
    rh,rw = max(2,int(positive(tile_mm,"Tile size")/dy)),max(2,int(tile_mm/dx))
    my,mx = int(float(margin_mm)/dy),int(float(margin_mm)/dx)
    if min(my,mx)<0 or my*2>=a.shape[0] or mx*2>=a.shape[1]: raise ValueError("Margins exclude the entire image/ROI.")
    threshold = positive(threshold_percent,"Threshold")/100
    mask = np.zeros(a.shape,bool); invalid_tiles = tested = 0
    for y in range(my,a.shape[0]-my,rh):
        for x in range(mx,a.shape[1]-mx,rw):
            tile = a[y:min(y+rh,a.shape[0]-my),x:min(x+rw,a.shape[1]-mx)]
            mean = float(tile.mean())
            if abs(mean)<1e-15: invalid_tiles+=1; continue
            mask[y:y+tile.shape[0],x:x+tile.shape[1]] = abs(tile-mean)>threshold*abs(mean)
            tested += tile.size
    yy,xx = np.nonzero(mask)
    ox,oy = (roi[0],roi[1]) if roi else (0,0)
    coordinates = np.column_stack((xx+ox,yy+oy))
    return {"count":int(mask.sum()),"coordinates_xy":coordinates,"mask":mask,"tested_pixels":tested,
            "zero_mean_tiles_skipped":invalid_tiles,"tile_mm":tile_mm,"margin_mm":margin_mm,
            "threshold_percent":threshold_percent}


def uniformity(image, spacing, roi=None, tile_mm=30., overlap=.5):
    a = crop(image,roi); dy,dx = spacing2d(spacing)
    rh,rw = max(2,int(positive(tile_mm,"Tile size")/dy)),max(2,int(tile_mm/dx))
    if not 0<=overlap<1 or rh>a.shape[0] or rw>a.shape[1]: raise ValueError("Uniformity tile/overlap does not fit ROI.")
    sy,sx = max(1,int(rh*(1-overlap))),max(1,int(rw*(1-overlap)))
    ys,xs = list(range(0,a.shape[0]-rh+1,sy)),list(range(0,a.shape[1]-rw+1,sx))
    means,sds = np.empty((len(ys),len(xs))),np.empty((len(ys),len(xs)))
    for j,y in enumerate(ys):
        for i,x in enumerate(xs):
            tile = a[y:y+rh,x:x+rw]; means[j,i]=tile.mean(); sds[j,i]=tile.std(ddof=1)
    if np.any(means<=0) or np.any(sds<=0): raise ValueError("Uniformity/SNR requires positive means and nonzero ROI noise; check dark correction/data.")
    snr = means/sds
    def global_nu(z): return float(200*(z.max()-z.min())/(z.max()+z.min()))
    def local_nu(z):
        if min(z.shape)<3: return None
        neighbors = (ndimage.convolve(z,np.ones((3,3)),mode="constant")-z)/8
        return float(np.max(abs((z[1:-1,1:-1]-neighbors[1:-1,1:-1])/neighbors[1:-1,1:-1]))*100)
    coverage = (ys[-1]+rh)*(xs[-1]+rw)/a.size*100
    return {"LSNU_percent":local_nu(means),"GSNU_percent":global_nu(means),
            "LSNRNU_percent":local_nu(snr),"GSNRNU_percent":global_nu(snr),
            "mean_signal":float(means.mean()),"mean_roi_sd":float(sds.mean()),"roi_count":means.size,
            "coverage_percent_of_selected_area":coverage,"signal_map":means,"snr_map":snr,
            "tile_mm":tile_mm,"overlap":overlap,"limits_source":"Legacy COQ defaults",
            "limits_percent":{"LSNU_percent":4.,"GSNU_percent":8.,"LSNRNU_percent":8.,"GSNRNU_percent":20.}}
