# Verification — Mammo QA Studio 1.0.0

Verified 8 November 2026 using Linux, Python 3.12, and a Tk virtual desktop.

| Check | Result |
|---|---|
| Automated numerical and file-format tests | 25 passed |
| GUI workflow | 14 recorded synthetic test runs |
| DICOM | Uncompressed multiframe decode, frame selection, stored pixel preservation, detector spacing priority/fallback, optional modality rescale, and ROI entry |
| Beam and ROI | kVp grouped errors/CV, logarithmic HVL, inverse-square output, manual MGD factors, SNR, both CNR denominators, ghost ratio from ROIs and entered means, uniformity, and defective pixels |
| MTF | Gaussian edge and slit targets in both orientations; expected MTF50 within the tested 4% tolerance, normalized DC, detector Nyquist support |
| NPS | Pixel-area normalization, white-noise variance, Hann window correction, quadratic detrending, and directional spectrum |
| DQE | Independently calculated curve point, direction and calibration checks, overlap-only frequency range, and in-GUI combination of separate edge/flat-field acquisitions |
| Export | PDF text and Greek characters, reopened XLSX/XLS numeric cells, CSV and JSON data, curve CSV, PNG plot, and 2D NPZ |
| Layout | Desktop viewer and MTF plot inspected; 10-page sample PDF and Excel Summary rendered and checked |

The tests use synthetic measurements and images. No manufacturer-specific calibration, clinical detector data, or Windows installation was tested. Confirm performance against your own validated QA workflow and enter the limits applicable to your system.
