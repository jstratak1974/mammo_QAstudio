# Mammo QA Studio

Mammography quality assurance in a single Python/Tkinter window. Load a detector DICOM, draw and name measurement ROIs, run the relevant test, and export a session containing the values, methods, and plots. The program runs locally.

## Install and start

**Windows:** Install Python 3.10 or later with Tcl/Tk and the Python launcher. Python 3.12 is the tested version. Extract the whole ZIP into a writable folder. Double-click `install_windows.bat` once, then `run_windows.bat`. The installer creates a virtual environment beside the app.

**Manual installation** on Windows, Linux, or macOS:

```text
python -m venv .venv
```

Activate the environment, then run:

```text
python -m pip install -r requirements.txt
python mammo_qa.py
```

On Windows, you can run `.venv\Scripts\python.exe` directly. On Linux install the distribution's Tk package if it is missing (e.g. `python3-tk` on Ubuntu); on macOS use a Python build with Tk. A desktop display is required. For optional JPEG/JPEG 2000 decoding, install the decoder packages in the same environment:

```text
python -m pip install pylibjpeg pylibjpeg-libjpeg pylibjpeg-openjpeg
```

## Quick start

1. Click **Load demo** to try synthetic images and meter readings. If you already have data open, the demo opens in a separate window.
2. **Beam → kVp → Calculate** produces a result grouped by nominal setting. Switch to **HVL** to see the attenuation example.
3. Select **Demo Slanted edge**, then **Transfer → MTF → Calculate**. The demo image has a prepared Analysis ROI.
4. Select **Demo Flat field 1** and **Demo Flat field 2** with Ctrl-click. Select **Transfer → NPS / NNPS**, choose **Selected images**, and calculate. Use the **Plot** view picker for the NPS, NNPS, and 2D spectra.
5. Select **Transfer → DQE**. Pick those two runs. For the demo only, the supplied values are 50 µGy and 5000 mm⁻²/µGy. Select **Confirmed** and calculate. For your measurements, replace both values and document the beam/q source.
6. Select **Demo Ghost and contrast** to try **Image QA → Ghost factor**, **SNR**, or **CNR / SDNR** using its prepared ROIs.
7. Enter your details on **Session** and use **Export report**. Keep real and synthetic results in separate sessions.

## DICOM and ROI workflow

**Open DICOM** accepts multiple grayscale files. For a multiframe object, enter the desired **one-based Frame** number before opening it; each selected file contributes that frame only. Frames are not assumed to be interchangeable flat fields. The analysis targets 2D projection/detector QA, not reconstructed tomosynthesis slices.

The viewer uses stored pixels by default. MONOCHROME1 changes display polarity only. Windowing, preview downsampling, and zoom never change the samples used for calculations. The optional **Apply DICOM modality transform when opening** is explicit and recorded. **Image info** shows the acquisition fields used for QA. The program prefers detector-plane spacing metadata and labels a PixelSpacing fallback for verification. Enter a manual override with **Pixel pitch** when the tag is missing, invalid, or describes a different plane.

Select a role (Analysis, Signal, Background, R1, R2, R3) and drag a rectangle. Alternatively enter exact zero-based **X, Y, W, H** values and press **Set**. **Full** assigns the full image to the active role; **Clear** removes that role. ROIs are stored independently for each loaded image. Press **Home** on the Matplotlib toolbar to restore the view after zooming. Use original detector/for-processing acquisitions for quantitative QA.

For measurements repeated at different technique, PMMA thickness, detector position, or dates, enter a meaningful **Run label**. Repeated results accumulate in **History**; select a row to inspect **Values / methods** or **Plot**. A new measurement does not replace an older measurement. Export before closing the application.

## Beam and image calculations

| Test | Inputs and calculation | Output |
|---|---|---|
| kVp | Paste set kVp and measured kVp, one reading per line. Group by set kVp under one beam/technique per run. | Per-reading signed error in kV and %, group mean error, max absolute error, sample SD, CV = 100 × SD/mean, max deviation from mean in kV. One reading does not give repeatability. |
| HVL | Paste added Al thickness in mm and measured air kerma, consistently in mGy or µGy. Include unfiltered readings and adjacent attenuated readings above/below 50%. The program averages repeats at each thickness and interpolates log kerma between the bracket points. | HVL in mm Al, measured bracket, attenuation plot, mean unfiltered kerma and first-to-last drift when repeated. No extrapolation. |
| Tube output | Paste mAs and kerma in **mGy**. Enter measured/reference focus-to-meter distances. | Output in mGy/mAs, reference-distance correction `(measured distance / reference distance)^2`, sample SD and CV. Use matched exposures for repeatability. |
| SNR | Homogeneous **Analysis** ROI; optional signal offset in the selected analysis units. | `(mean − offset) / sample SD` and ROI statistics. |
| CNR / SDNR | Place **Signal** and **Background** ROIs in the detail and nearby background. Select background sample SD or RMS of the two sample SDs. | `abs(mean Signal − mean Background) / chosen noise`, with both means, sample SDs, ROI coordinates and area when known. |
| Ghost factor | Use the three numbered regions on the second exposure in **EUREF Figure 2.2**, or enter their means. Use matched area ROIs. | Signed `(R3 − R2)/(R1 − R2)`, magnitude, and reference contrast. The optional limit applies to the magnitude. A zero reference contrast is rejected. |
| Uniformity | Analysis ROI on a uniform image; enter tile size and overlap. | Global and local signal and SNR nonuniformity, coverage, and tile maps. |
| Defective pixels | Analysis ROI on a uniform image; set tile size, exclusion border, and local deviation threshold. | Count and coordinates of pixels beyond the local tile-mean threshold. |
| 2D glandular dose | Enter incident air kerma at the entrance plane without backscatter, HVL, PMMA thickness and independently verified `g`, `c`, `s`. | `K × g × c × s` in mGy. HVL and PMMA describe the factor choice; the program does not infer factors or use the DICOM organ dose. |

Paste two-column input with commas, tabs, spaces or semicolons. For decimal commas, use **semicolons** or **tabs** between columns (example: `28;27,95`). Use no header, or prefix a header/comment with `#`. Use the same target/filter and conditions for a grouped kVp or HVL run. Record the paddle position for HVL.

Limits are optional and entered for your protocol; the app does not apply universal thresholds. Where present, bounds are inclusive. An out-of-limit result and an unassessed result are distinct. Check the acceptance definition and minimum repeat count for your own procedure.

## Detector response, MTF, NPS and DQE

- **Response calibration:** paste measured detector kerma (µGy) and mean stored signal for at least four exposures. Or pair a measured kerma with an image's **Analysis** mean using **Append active ROI mean**. Fit linear, log, log(1+cK), or square-root response. The inverse response can be applied to samples for analysis; the viewer remains in stored values.
- **MTF:** select one clean slanted edge (or slit) in the **Analysis** ROI. Use verified detector pitch, choose method/orientation, and adjust the profile length and oversampling if the geometry is rejected. The algorithm bins samples by normal distance to the feature, derives the LSF, and reports its normalized Fourier magnitude up to detector Nyquist. Results include MTF50, MTF20, MTF10, values at 2.5/5/7.5 lp/mm where sampled, and ESF/LSF plots. A vertical edge gives horizontal frequency response. A slit width of 0 disables slit aperture correction.
- **NPS / NNPS:** use uniform matched exposures. Choose a single image or Ctrl-select a set, with the same geometry, pitch and beam/exposure. The active Analysis ROI is applied at identical coordinates on every selected image. Use square pixels for this version's directional extraction. Fragment size must be even (16–1024 px) and fit within the ROI. Set overlap, mean/plane/quadratic detrending, optional Hann window, and frequency-axis bands. NPS uses a two-sided, pixel-area and window-power normalized FFT in signal²·mm². NNPS divides each fragment's power by its squared mean, then averages in mm². You can inspect/export horizontal, vertical, directional average, radial, and 2D spectra.
- **DQE:** choose MTF and NNPS results in the same detector geometry and with the same calibration. The software selects NNPS in the MTF's frequency direction and interpolates MTF on their common frequency support. Enter detector-plane air kerma **K in µGy** and matching beam-specific input quanta per air kerma **q in mm⁻²/µGy**. `DQE = MTF²/(NNPS × K × q)`. Confirm suitability of the acquisition and linearized, offset-corrected signal. An entered beam label records the q source; q is not inferred from the DICOM kVp or from a vendor name. Values above 100% remain visible and are flagged for investigation.

The detector calculations build on the earlier independent COQ Python implementation. Matching standardized measurement geometry, detector mode, data treatment and reference spectra is the operator's responsibility. The app has not been validated against your detector acquisitions or certified for IEC compliance.

## Exports

| Format | Contents |
|---|---|
| PDF | Paginated A4 report, session details, test results, input settings, selected measurement tables, and summary plots. |
| XLSX | Summary, acquisition/settings notes, numeric detail tables, full sampled curves, and charts on frequency-curve sheets. |
| XLS | Legacy Excel 97–2003 workbook with numeric tables. |
| CSV / JSON | Session values, detailed inputs, and curve tables for external analysis. JSON does not contain source images. |
| Table CSV / Plot PNG | The current result's selected table or plot. |
| Spectrum NPZ | Selected NPS result's full `nps2d`, `nnps2d`, and `frequency_axis` arrays. |

Results are exported with neutral acquisition labels and selected technical tags, without patient names or embedded source pixels. The window title, file chooser and viewer are local. Only share reports and user-entered labels appropriate for the intended audience.

## Source files and verification

- `mammo_qa.py` — Tkinter entry point and workflows.
- `qa_metrics.py` — measurement functions and single-frame DICOM reader.
- `coq_core.py` — reused detector math, derived from the earlier COQ reimplementation.
- `qa_reports.py` — report formats and chart styling.
- `tests/test_metrics.py` — analytical checks and DICOM/format tests.
- `tests/gui_smoke.py` — synthetic end-to-end desktop workflow and report export verification.

```text
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/test_metrics.py
python tests/gui_smoke.py
```

The numerical suite and GUI export flow were exercised on Linux/Python 3.12. The GUI smoke test requires a display. The Windows launchers were inspected; they were not executed on Windows.

## Method references

- [IAEA Human Health Series No. 17: Quality Assurance Programme for Digital Mammography](https://www-pub.iaea.org/MTCD/Publications/PDF/Pub1482_web.pdf), including HVL, SDNR, MTF and glandular dose procedures.
- [EUREF European Protocol, fourth edition and updates](https://euref.org/download-section/physico-technical-protocol/), digital mammography §2b.2.4.5 and Figure 2.2 for ghosting.
- Donini B, Rivetti S, Lanconelli N, Bertolini M. *Free software for performing physical analysis of systems for digital radiography and mammography*. Medical Physics, 2014. [DOI](https://doi.org/10.1118/1.4870955).
- [New Software for DQE Calculation in Digital Mammography Compliant with IEC 62220–1-2](https://doi.org/10.1007/s10278-021-00546-y), for input quanta and measurement context. This reference does not imply compliance of this app.
