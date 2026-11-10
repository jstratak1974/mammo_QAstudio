"""Numerical tools for Mammo QA Studio. No GUI or report dependencies."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

import coq_core as coq

VERSION = "1.0.0"
REFERENCES = {
    "IAEA HHS 17": "https://www-pub.iaea.org/MTCD/Publications/PDF/Pub1482_web.pdf",
    "EUREF 4th edition": "https://euref.org/download-section/physico-technical-protocol/",
    "Detector characterization": "https://doi.org/10.1118/1.4870955",
    "Mammography DQE": "https://doi.org/10.1007/s10278-021-00546-y",
}


def finite(value, name):
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"Enter a numeric value for {name}.") from exc
    if not math.isfinite(n):
        raise ValueError(f"{name} must be finite.")
    return n


def number(value, name, *, optional=False, minimum=None):
    if optional and (value is None or not str(value).strip()):
        return None
    n = finite(str(value).strip().replace(",", "."), name)
    if minimum is not None and n < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}.")
    return n


def pairs(text, names):
    """Parse pairs; decimal commas are accepted with semicolon or tab separators."""
    rows = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = (line.split(";") if ";" in line else line.split("\t") if "\t" in line
                  else line.split(",") if "," in line else re.split(r"\s+", line))
        if len(tokens) != 2:
            raise ValueError(f"Line {i}: enter exactly two columns ({', '.join(names)}).")
        rows.append(tuple(number(x, f"line {i}, {key}") for x, key in zip(tokens, names)))
    if not rows:
        raise ValueError(f"Enter measurements for {', '.join(names)}.")
    return rows


@dataclass
class Metric:
    label: str
    value: float | int | str | None
    unit: str = ""
    criterion: str = ""
    status: str = ""


@dataclass
class Table:
    title: str
    columns: tuple
    rows: list


@dataclass
class Chart:
    title: str
    xlabel: str
    ylabel: str
    series: list                  # [(label, x values, y values)]
    log: bool = False
    marker: str = ""


@dataclass
class Result:
    test: str
    label: str
    metrics: list[Metric]
    tables: list[Table] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)
    arrays: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    id: int = 0


def bounded(label, value, unit="", lo=None, hi=None):
    rule = " to ".join((f"{lo:g}", f"{hi:g}")) if lo is not None and hi is not None else (
        f">= {lo:g}" if lo is not None else f"<= {hi:g}" if hi is not None else "")
    status = ""
    if rule:
        status = ("Not assessed" if value is None else
                  "PASS" if (lo is None or value >= lo) and (hi is None or value <= hi) else "FAIL")
    return Metric(label, value, unit, rule, status)


def repeat_stats(a):
    a = np.asarray(a, dtype=float)
    if not len(a) or not np.all(np.isfinite(a)) or np.any(a <= 0):
        raise ValueError("Repeated meter readings must be finite and positive.")
    mean = float(a.mean())
    sd = float(a.std(ddof=1)) if len(a) >= 2 else None
    return mean, sd, None if sd is None else 100 * sd / mean


def voltage(readings, accuracy_limit=None, cv_limit=None, label=""):
    """One reading per row, grouped by set kVp; use a common beam setup per run."""
    groups = {}
    for nominal, measured in readings:
        nominal = coq.positive(nominal, "Set kVp")
        measured = coq.positive(measured, "Measured kVp")
        groups.setdefault(nominal, []).append(measured)
    if not groups:
        raise ValueError("Enter at least one voltage reading.")
    metrics, summary, raw, curves = [], [], [], []
    for nominal, values in sorted(groups.items()):
        a = np.array(values)
        mean, sd, cv = repeat_stats(a)
        dev = (a-nominal)/nominal*100
        max_abs = float(np.max(np.abs(dev)))
        max_dev_mean = float(np.max(np.abs(a-mean))) if len(a) > 1 else None
        acc = bounded(f"{nominal:g} kVp: max |error|", max_abs, "%", hi=accuracy_limit)
        rep = bounded(f"{nominal:g} kVp: CV", cv, "%", hi=cv_limit)
        metrics.extend((acc, rep))
        summary.append((nominal, len(a), mean, mean-nominal, float(dev.mean()), max_abs, sd, cv,
                        max_dev_mean, acc.status, rep.status))
        raw.extend((nominal, i, v, v-nominal, e) for i, (v, e) in enumerate(zip(a, dev), 1))
        curves.append((f"{nominal:g} kVp", np.arange(1, len(a)+1), dev))
    return Result("kVp", label, metrics, [
        Table("By set kVp", ("Set kVp", "n", "Mean kVp", "Mean error kV", "Mean error %", "Max |error| %",
                            "SD kV", "CV %", "Max dev. from mean kV", "Accuracy", "Repeat."), summary),
        Table("Meter readings", ("Set kVp", "Repeat", "Measured kVp", "Error kV", "Error %"), raw)],
        [Chart("kVp accuracy by repeat", "Repeat", "Signed error (%)", curves, marker="o")],
        ["Accuracy uses the largest absolute individual error per setting. CV uses sample SD (n−1). "
         "A single reading assesses accuracy only; use the repeat count and limit required by your protocol."],
        {"Accuracy limit (%)": accuracy_limit, "CV limit (%)": cv_limit})


def hvl(readings, minimum=None, maximum=None, label="", unit="mGy", kvp=None, beam="",
        paddle="Unspecified"):
    """Mean readings at each thickness; interpolate log kerma at 50%."""
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("HVL minimum exceeds maximum.")
    if unit not in ("mGy", "µGy"):
        raise ValueError("Select mGy or µGy consistently for all readings.")
    groups = {}
    for x, y in readings:
        x = number(x, "Al thickness", minimum=0)
        groups.setdefault(x, []).append(coq.positive(y, "Kerma reading"))
    if 0. not in groups or len(groups) < 2:
        raise ValueError("Enter 0 mm Al readings and attenuated readings bracketing 50%.")
    points = sorted((x, float(np.mean(v)), len(v)) for x, v in groups.items())
    x = np.array([p[0] for p in points]); y = np.array([p[1] for p in points])
    target = y[0]/2
    exact = np.flatnonzero(np.isclose(y, target, rtol=1e-12, atol=0))
    crosses = np.flatnonzero((y[:-1]-target)*(y[1:]-target) < 0)
    if len(exact)+len(crosses) != 1 or (len(crosses) and y[crosses[0]] < target):
        raise ValueError("A single descending 50% bracket is required; check for missing points or repeated crossings.")
    if len(exact):
        i = int(exact[0])
        if y[i-1] <= target or (i+1 < len(y) and y[i+1] >= target):
            raise ValueError("The exact 50% sample does not belong to a descending crossing.")
        estimate = x[i]; bracket = (x[i], x[i])
    else:
        i = int(crosses[0])
        estimate = x[i] + (x[i+1]-x[i])*math.log(target/y[i])/math.log(y[i+1]/y[i])
        bracket = (x[i], x[i+1])
    unfiltered = groups[0.]
    drift = (100*(unfiltered[-1]-unfiltered[0])/unfiltered[0]) if len(unfiltered)>1 else None
    notes = ["Interpolates between adjacent readings in log kerma at 50% of the mean unfiltered reading.",
             "Record paddle position and target/filter. Use a constant technique and narrow-beam measurement geometry."]
    if np.any(np.diff(y)>0):
        notes.append("Attenuation readings increase in at least one interval. Review measurement scatter or drift.")
    if bracket[0] == 0 and bracket[1] > 0:
        notes.append("The bracket starts at zero thickness. Add measurements close to 50% for a better HVL estimate.")
    return Result("HVL", label, [bounded("Half-value layer", float(estimate), "mm Al", minimum, maximum),
                  Metric("Unfiltered mean", y[0], unit), Metric("50% of unfiltered mean", target, unit),
                  Metric("Unfiltered first-to-last change", drift, "%")],
                  [Table("Attenuation means", ("Al thickness (mm)", "Mean kerma ("+unit+")", "n", "Transmission %"),
                         [(a,b,n,100*b/y[0]) for a,b,n in points]),
                   Table("Original meter readings", ("Al thickness (mm)", "Kerma ("+unit+")"), list(readings))],
                  [Chart("HVL attenuation", "Added Al (mm)", "Transmission (%)",
                         [("Measurements", x, 100*y/y[0]), ("50% level", [0,x[-1]], [50,50]),
                          ("HVL", [estimate,estimate], [min(50,100*y.min()/y[0]),100])], log=True, marker="o")],
                  notes, {"Beam": beam, "Measured kVp": kvp, "Paddle": paddle, "Bracket (mm Al)": bracket})


def output_repeatability(readings, distance_mm, reference_mm, cv_limit=None, label="", beam=""):
    """Input (mAs, mGy), output corrected to selected reference distance."""
    distance = coq.positive(distance_mm, "Meter distance")
    reference = coq.positive(reference_mm, "Reference distance")
    rows = []
    for mas, kerma in readings:
        mas = coq.positive(mas, "mAs")
        kerma = coq.positive(kerma, "Air kerma (mGy)")
        rows.append((mas, kerma, kerma/mas, kerma/mas*(distance/reference)**2))
    if not rows:
        raise ValueError("Enter at least one output reading.")
    values = [row[3] for row in rows]
    avg, sd, cv = repeat_stats(values)
    return Result("Tube output", label, [Metric("Mean output at reference", avg, "mGy/mAs"),
                  Metric("Sample SD", sd, "mGy/mAs"), bounded("Output CV", cv, "%", hi=cv_limit)],
                  [Table("Output readings", ("mAs", "Measured kerma (mGy)", "Measured mGy/mAs", "Ref. mGy/mAs"), rows)],
                  [Chart("Tube output", "Reading", "mGy/mAs at reference",
                         [("Output", list(range(1,len(rows)+1)), values)], marker="o")],
                  ["Use repeats at fixed technique for reproducibility; different mAs values describe output consistency."],
                  {"Beam": beam, "Meter distance (mm)": distance, "Reference distance (mm)": reference})


def glandular_dose(kerma_mgy, g, c, s, hvl_mm, pmma_mm, label="", limit=None, reference=""):
    factors = [coq.positive(v,k) for v,k in zip((g,c,s),("g","c","s"))]
    kerma = coq.positive(kerma_mgy, "Incident air kerma")
    hvl_mm = coq.positive(hvl_mm, "HVL")
    pmma_mm = coq.positive(pmma_mm, "PMMA thickness")
    value = kerma * math.prod(factors)
    return Result("Mean glandular dose", label,
                  [bounded("Mean glandular dose", value, "mGy", hi=limit), Metric("Incident air kerma", kerma, "mGy")],
                  inputs={"HVL (mm Al)": hvl_mm, "PMMA thickness (mm)": pmma_mm, "g": factors[0],
                          "c": factors[1], "s": factors[2], "Factor source / beam": reference},
                  notes=["K × g × c × s using entered factors. K is incident air kerma at the entrance plane without "
                         "backscatter; factors must correspond to the beam and equivalent breast thickness. 2D only."])


def subregion(image, roi):
    # Validate only the crop, keeping full DICOM arrays in their original dtype.
    a = np.asarray(image)
    if a.ndim != 2:
        raise ValueError("Choose a grayscale frame.")
    x,y,w,h = roi
    if min(x,y)<0 or min(w,h)<2 or x+w>a.shape[1] or y+h>a.shape[0]:
        raise ValueError("ROI lies outside the selected image or is smaller than 2 × 2 pixels.")
    return coq.image2d(a[y:y+h,x:x+w])


def roi_stat(a):
    return float(np.mean(a)), float(np.std(a, ddof=1))


def disjoint(roles, rois):
    for i, r in enumerate(roles):
        x,y,w,h = rois[r]
        for other in roles[:i]:
            u,v,p,q = rois[other]
            if x<u+p and u<x+w and y<v+q and v<y+h:
                raise ValueError(f"ROIs {r} and {other} overlap.")


def roi_quality(mode, image, rois, *, spacing=None, offset=0., noise="Background SD", limit=None, label="", transform=None):
    roles = {"SNR": ["Analysis"], "CNR / SDNR": ["Signal","Background"],
             "Ghost factor": ["R1","R2","R3"]}[mode]
    missing = [r for r in roles if r not in rois]
    if missing:
        raise ValueError("Draw or set required ROI(s): "+", ".join(missing)+".")
    disjoint(roles, rois)
    crops = {r: subregion(image, rois[r]) for r in roles}
    stat = {r: roi_stat(transform(a) if transform else a) for r,a in crops.items()}
    table = Table("ROI measurements", ("ROI","x","y","Width (px)","Height (px)","Area (mm²)","Mean","Sample SD"),
                  [(r,*rois[r],(rois[r][2]*rois[r][3]*math.prod(spacing)) if spacing else None,
                    *stat[r]) for r in roles])
    inputs = {"ROI coordinates": "zero-based x, y, width, height", "Pixel spacing (dy, dx) mm":spacing}
    if mode == "SNR":
        mu,sd = stat["Analysis"]
        signal = coq.positive(mu-offset, "Mean minus offset")
        value = signal/coq.positive(sd, "ROI sample SD")
        metrics = [bounded("SNR", value, hi=None, lo=limit), Metric("ROI mean",mu),Metric("ROI sample SD",sd)]
        notes = ["SNR = (mean signal − entered offset) / sample SD."]
        inputs["Signal offset"] = offset
    elif mode == "CNR / SDNR":
        a, sa = stat["Signal"]; b, sb = stat["Background"]
        den = sb if noise == "Background SD" else math.sqrt((sa*sa+sb*sb)/2)
        value = abs(a-b)/coq.positive(den, "Noise denominator")
        metrics = [bounded("CNR / SDNR", value, lo=limit), Metric("Signal minus background", a-b),
                   Metric("Noise denominator",den)]
        notes = ["Magnitude of mean difference divided by "+noise+
                 ". Background SD and the RMS of the two ROI SDs are different protocol conventions."]
        inputs["Noise definition"] = noise
    else:
        values = [stat[r][0] for r in roles]
        result = ghost(*values, limit=limit, label=label)
        if len({rois[r][2]*rois[r][3] for r in roles})>1:
            result.notes.append("The three ROI areas differ. Use matched areas for the reference protocol.")
        result.tables.append(table)
        result.inputs.update(inputs)
        return result
    return Result(mode,label,metrics,[table],notes=notes,inputs=inputs)


def ghost(r1, r2, r3, limit=None, label=""):
    r1,r2,r3 = [finite(v,"ROI mean") for v in (r1,r2,r3)]
    contrast = r1-r2
    if abs(contrast) <= 1e-12 * max(abs(r1), abs(r2), 1):
        raise ValueError("R1 and R2 have the same mean; the Al reference contrast is zero.")
    ratio = (r3-r2)/contrast
    return Result("Ghost factor",label,[Metric("Signed ghost factor",ratio),
                  bounded("Absolute ghost factor",abs(ratio),hi=limit), Metric("Ghost magnitude",abs(ratio)*100,"%"),
                  Metric("Reference contrast R1 − R2",contrast)],
                  notes=["EUREF 4th ed., Fig. 2.2: (R3 − R2) / (R1 − R2) on the second exposure. "
                         "The entered action limit applies to the magnitude. Verify placement in the numbered regions."],
                  inputs={"R1 mean":r1,"R2 mean":r2,"R3 mean":r3})


def spatial_mtf(image, spacing, *, method, orientation, oversampling, profile, slit_width, label=""):
    out = coq.mtf(image, spacing, method=method, orientation=orientation, oversampling=oversampling,
                  profile_mm=profile, slit_width_mm=slit_width)
    f,m = out["frequency"],out["mtf"]
    direction = "Horizontal" if out["orientation"] == "vertical" else "Vertical"
    met = [Metric(f"MTF{p}",coq.mtf_crossing(f,m,p/100),"lp/mm") for p in (50,20,10)]
    for point in (2.5,5.,7.5):
        met.append(Metric(f"MTF at {point:g} lp/mm",float(np.interp(point,f,m)) if f[0]<=point<=f[-1] else None))
    met += [Metric("Nyquist",out["nyquist_lp_mm"],"lp/mm"),Metric("Feature angle",out["angle_deg"],"deg"),
            Metric("Trace RMS residual",out["trace_rmse_px"],"px"),Metric("Frequency direction",direction)]
    tables = [Table("MTF curve",("Frequency (lp/mm)","MTF"),list(zip(f,m))),
              Table("ESF or slit profile",("Normal distance (mm)","Signal"),list(zip(out["profile_x_mm"],out["profile"]))),
              Table("LSF",("Normal distance (mm)","LSF"),list(zip(out["lsf_x_mm"],out["lsf"])))]
    return Result("MTF",label,met,tables,[
        Chart("Presampled MTF","Spatial frequency (lp/mm)","MTF",[(direction,f,m)]),
        Chart("Edge or slit profile","Normal distance (mm)","Signal",[("Profile",out["profile_x_mm"],out["profile"])]),
        Chart("Line-spread function","Normal distance (mm)","LSF",[("LSF",out["lsf_x_mm"],out["lsf"])])],
        ["Frequency direction is normal to the edge or slit. Results are in detector-plane units for detector pitch.",
         "Crossings or reference frequencies outside sampled support are blank."],
        {"Method":method,"Feature orientation":out["orientation"],"Oversampling":oversampling,
         "Actual profile (mm)":out["actual_profile_mm"],"Slit width correction (mm)":slit_width,
         "Empty bin fraction":out["empty_bin_fraction"]},
        {"frequency":f,"mtf":m,"direction":direction})


def spectral_curves(matrix, omit, lines):
    n = matrix.shape[0]; center=n//2
    positive = np.arange(center+1,n); negative = 2*center-positive
    offsets = np.arange(omit,omit+lines)
    band=np.unique(np.r_[center+offsets,center-offsets])
    h=(matrix[np.ix_(band,positive)].mean(0)+matrix[np.ix_(band,negative)].mean(0))/2
    v=(matrix[np.ix_(positive,band)].mean(1)+matrix[np.ix_(negative,band)].mean(1))/2
    y,x=np.indices(matrix.shape)-center
    radius=np.floor(np.hypot(x,y)).astype(int)
    mask=(abs(x)>=omit)&(abs(y)>=omit)&(radius<center)
    num=np.bincount(radius[mask],weights=matrix[mask],minlength=center)
    den=np.bincount(radius[mask],minlength=center)
    radial=np.divide(num,den,out=np.full(center,np.nan),where=den>0)[1:]
    return {"Horizontal":h,"Vertical":v,"HV average":(h+v)/2,"Radial":radial}


def noise_spectrum(images, spacing, *, fragment, overlap, detrend, window, omit, lines, label=""):
    if fragment > 1024:
        raise ValueError("Choose a fragment size of 1024 pixels or less.")
    for a in images:
        if np.mean(a)<=0:
            raise ValueError("Mean flat-field signal must be positive after offset correction.")
    out=coq.nps(images,spacing,fragment=fragment,overlap=overlap,detrend=detrend,window=window,
                omit=omit,lines=lines,reject_coq=False)
    f=out["frequency"]
    curves=spectral_curves(out["nps2d"],omit,lines)
    normalized=spectral_curves(out["nnps2d"],omit,lines)
    def table(title, series, unit):
        return Table(title,("Frequency (lp/mm)",)+tuple(k+" ("+unit+")" for k in series),
                     list(zip(f,*series.values())))
    def chart(title,series,unit):
        return Chart(title,"Spatial frequency (lp/mm)",unit,[(k,f,v) for k,v in series.items()], log=True)
    df=abs(float(out["frequency_axis"][1]-out["frequency_axis"][0]))
    return Result("NPS / NNPS",label,[Metric("Images",len(images)),Metric("Accepted fragments",out["accepted_fragments"]),
                  Metric("Mean signal",out["mean_signal"]),Metric("Integrated NPS",float(out["nps2d"].sum())*df**2,"signal²")],
                  [table("NPS curves",curves,"signal²·mm²"),table("NNPS curves",normalized,"mm²")],
                  [chart("Normalized noise power",normalized,"NNPS (mm²)"),chart("Noise power",curves,"NPS (signal²·mm²)")],
                  ["Use matched unprocessed flat fields at one beam and exposure. NPS uses pixel area and window-power "
                   "normalization; NNPS uses the squared mean of each fragment before averaging."],
                  {"Fragment (px)":fragment,"Overlap":overlap,"Detrend":detrend,"Window":window,
                   "Axis lines omitted":omit,"Lines per side":lines},
                  {"frequency":f,"nnps_curves":normalized,"nps2d":out["nps2d"],"nnps2d":out["nnps2d"],
                   "frequency_axis":out["frequency_axis"]})


def detector_dqe(mtf, nps, kerma, q_per_uGy, *, direction=None, beam="", label=""):
    direction = direction or mtf.arrays["direction"]
    if direction != mtf.arrays["direction"]:
        raise ValueError("MTF and NNPS must use the same frequency direction.")
    if mtf.inputs.get("Signal calibration") != nps.inputs.get("Signal calibration"):
        raise ValueError("MTF and NPS were measured with different signal calibrations.")
    if mtf.inputs.get("DICOM modality transform") != nps.inputs.get("DICOM modality transform"):
        raise ValueError("MTF and NPS used different DICOM modality-transform settings.")
    a,b=mtf.inputs.get("Pixel spacing (dy, dx) mm"),nps.inputs.get("Pixel spacing (dy, dx) mm")
    if a is not None and b is not None and not np.allclose(a,b,rtol=.001,atol=0):
        raise ValueError("MTF and NPS pixel spacings differ. Use the same detector sampling mode.")
    f = nps.arrays["frequency"]; v = nps.arrays["nnps_curves"][direction]
    overlap = (f>=mtf.arrays["frequency"][0]) & (f<=mtf.arrays["frequency"][-1])
    if np.any(v[overlap]<=0) or not np.all(np.isfinite(v[overlap])):
        raise ValueError("Selected NNPS has a zero or invalid value in the overlapping range.")
    out=coq.dqe(mtf.arrays["frequency"],mtf.arrays["mtf"],f,v,kerma,q_per_uGy)
    f,d=out["frequency"],out["dqe"]
    metrics=[Metric("Direction",direction), Metric("Detector air kerma",kerma,"µGy"),
             Metric("Input quanta density K × q",out["q"],"mm⁻²"), Metric("Max DQE",float(d.max()*100),"%"),
             Metric("Samples above 100%",out["values_above_one"])]
    for point in (1.,2.,2.5,5.):
        metrics.append(Metric(f"DQE at {point:g} lp/mm",float(np.interp(point,f,d))*100 if f[0]<=point<=f[-1] else None,"%"))
    notes=["DQE = MTF² / (NNPS × K × q) on common sampled frequency support. Use detector-plane air kerma "
           "and q for the matching beam, not breast-entrance kerma or DICOM organ dose."]
    if out["values_above_one"]:
        notes.append("DQE exceeds 100% at some samples. Inspect beam q, kerma, linearization and acquisition/analysis settings. Values are retained.")
    return Result("DQE",label,metrics,[Table("DQE curve",("Frequency (lp/mm)","DQE fraction","DQE %","MTF","NNPS (mm²)"),
                  list(zip(f,d,100*d,out["interpolated_mtf"],out["nnps"])))],
                  [Chart("Detective quantum efficiency","Spatial frequency (lp/mm)","DQE (%)",[(direction,f,100*d)])],
                  notes,{"MTF result":mtf.id,"NPS result":nps.id,"Beam / q source":beam,
                         "q (mm⁻²/µGy)":q_per_uGy,"Linearization confirmed":True})


def load_dicom(path, frame=0, apply_rescale=False):
    """Decode one selected frame and keep only acquisition metadata needed for QA."""
    import pydicom
    from pydicom.pixels import pixel_array, apply_modality_lut
    p=Path(path)
    ds=pydicom.dcmread(p,stop_before_pixels=True)
    if int(getattr(ds,"SamplesPerPixel",1))!=1:
        raise ValueError("This viewer accepts grayscale detector images only.")
    frames=int(getattr(ds,"NumberOfFrames",1))
    if not 0 <= frame < frames:
        raise ValueError(f"Frame must be between 1 and {frames}.")
    try:
        a=pixel_array(p,index=frame)
    except Exception as exc:
        raise ValueError("Unable to decode pixel data. Install the optional JPEG/JPEG 2000 decoders "
                         "or export an uncompressed DICOM. "+str(exc)) from exc
    if a.ndim!=2 or min(a.shape)<2 or not np.isfinite(a).all():
        raise ValueError("The selected frame must be a finite grayscale image of at least 2 × 2 pixels.")
    shared=getattr(ds,"SharedFunctionalGroupsSequence",[])
    per=getattr(ds,"PerFrameFunctionalGroupsSequence",[])
    groups=([per[frame]] if frame<len(per) else [])+list(shared[:1])
    values={}
    for key in ("Modality","PresentationIntentType","PhotometricInterpretation","KVP","Exposure",
                "ExposureInuAs","ExposureTime","BodyPartThickness","AnodeTargetMaterial","FilterMaterial",
                "DistanceSourceToDetector","LossyImageCompression","RescaleSlope","RescaleIntercept"):
        val=coq._value(ds,key)
        if val is not None:
            values[key]=val
    spacing=None
    def read_spacing(value):
        try:
            return coq.spacing2d(value) if value is not None and len(value)==2 else None
        except (TypeError, ValueError, IndexError):
            return None
    for key in ("ImagerPixelSpacing","NominalScannedPixelSpacing"):
        for group in groups:
            seq=getattr(group,"XRayFramePixelDataPropertiesSequence",[])
            if seq:
                spacing=read_spacing(getattr(seq[0],key,None))
                if spacing is not None:
                    values["Spacing source"]="Frame "+key;break
        if spacing is None:
            spacing=read_spacing(getattr(ds,key,None))
            if spacing is not None:values["Spacing source"]=key
        if spacing is not None:
            break
    if spacing is None:
        for group in groups:
            seq=getattr(group,"PixelMeasuresSequence",[])
            if seq:
                spacing=read_spacing(getattr(seq[0],"PixelSpacing",None))
                if spacing is not None:
                    values["Spacing source"]="Frame PixelSpacing (verify plane)";break
    if spacing is None:
        spacing=read_spacing(getattr(ds,"PixelSpacing",None))
        if spacing is not None:values["Spacing source"]="PixelSpacing (verify plane)"
    if apply_rescale:
        transforms=[getattr(g,"PixelValueTransformationSequence",[]) for g in groups]
        transform=next((t[0] for t in transforms if t),ds)
        a=apply_modality_lut(a,transform)
        if not np.isfinite(a).all():
            raise ValueError("Modality transform produced non-finite values.")
    values.update({"Frame":frame+1,"Number of frames":frames,"Modality transform applied":bool(apply_rescale)})
    return coq.ImageRecord(p.name,a,spacing,values,str(p))
