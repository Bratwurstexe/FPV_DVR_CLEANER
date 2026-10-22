# Local footage and signal profiles

The development footage was copied from the project owner's recordings to
`X:/FPV_DVR_CLEANER/FPV_DVR_CLEANER/.scratch/training/`. The seven copies are
kept out of Git. Their byte lengths were checked against the source files.

| Group | Source | Files | Profile samples |
| --- | --- | ---: | ---: |
| Analog | `G:/Harburg/analog*.ts` and `G:/Videos/Bremerhaven/*.ts` | 5 | 1,333 frames |
| HDZero | `G:/Harburg/hdzero*.ts` | 2 | 1,278 frames |

The earlier statistical profiles are at `.scratch/training/profiles/analog-signal.json` and
`.scratch/training/profiles/digital-signal.json`. Each was calibrated for 90
seconds from its own group. They can be loaded through **Model room** in the
earlier Qt prototype. The .NET app lets users choose their own footage folder,
signal type, and learning duration in minutes or hours.

## Neural profiles and 15-second comparison

The new C# desktop app trains separate blind-neighborhood neural profiles.
One-minute local runs produced `.scratch/training/profiles/analog-neural.json`
from all five Analog recordings (45,312 sampled pixels; held-out mean absolute
prediction error 0.00629 in normalized pixel units) and
`.scratch/training/profiles/digital-neural.json` from the two HDZero recordings.
These profiles are selected in **Improve → Trained model**. The application
keeps Analog and HDZero choices separate. They remain ignored by Git.

For a visual check, 15 seconds centered on the 149.0167-second Bremerhaven
recording `20260220-173712.ts` were processed with the Analog model. Both
outputs have 900 frames at 60 fps and the same 15.000-second duration:

- Original cut: `.scratch/training/analog-middle-15s-original.mp4`
- Model output: `.scratch/training/analog-middle-15s-cleaned.mp4`
- Paired images at 2, 7.5, and 13 seconds: `.scratch/training/analog-middle-comparison/`

The average fine chroma texture in those three decoded frames changed from
0.2131 to 0.2023, 0.1885 to 0.1738, and 0.2014 to 0.1894 respectively.
That is a roughly 5–8% decrease per sampled frame. It may reflect less color
noise, but this metric cannot distinguish noise from real color detail. Visual
review shows a subtle change and no broad restoration claim is made.

## Harburg disturbance check

An automated contact-sheet scan of the four Harburg copies found prominent
HDZero corruption in `hdzero1.ts` around 25–26 seconds: colored block specks
and a wide damaged horizontal region. The interval 24–28 seconds was exported
with the local HDZero neural profile. Both the unprocessed cut and model output
contain 240 frames at 60 fps and are stored as
`.scratch/training/harburg-hdzero-glitch-original.mp4` and
`.scratch/training/harburg-hdzero-glitch-cleaned.mp4`. Synchronized images are
in `.scratch/training/harburg-hdzero-comparison/`.

At 25 and 26 seconds in the source, decoded chroma texture fell from 1.323 to
0.993 and from 0.473 to 0.406 respectively. Some colored specks are reduced,
but the wide corrupted band and many blocks remain. This model only predicts
from nearby pixels in the same frame; the result demonstrates a current limit,
not recovery of the hidden image.

## Continued five-frame Analog model

The .NET training screen offers **New five-frame model** and **Continue an
existing model**. The network uses spatial neighbors from the current frame
and pixels from two earlier and two later frames aligned with optical flow.
Continuing starts with the selected model's weights and saves a separate JSON
version; old single-frame neural profiles can be upgraded. A 60-second local
continuation of `analog-neural.json` on the five Analog copies produced
`.scratch/training/profiles/analog-motion5-continued.json` (45,312 newly sampled
pixels; 90,624 cumulative; held-out prediction MAE 0.00627). The holdout target
is still damaged footage, so this number is not a measure of true restoration.

For Analog, `analog2.ts` has visible rainbow and horizontal color interference
around 236–240 seconds. An unprocessed cut and the earlier single-frame model
output are at `.scratch/training/harburg-analog-stripes-original.mp4` and
`.scratch/training/harburg-analog-stripes-cleaned.mp4`. The five-frame output is
`.scratch/training/harburg-analog-stripes-motion5.mp4`; synchronized comparison
images are in `.scratch/training/harburg-analog-stripes-motion5-comparison/`.
At 2 seconds into the cut, fine chroma texture was 0.690 in the original,
0.539 with the earlier model, and 0.513 with the five-frame version. Across
six sampled frames, the five-frame texture metric was below the old result in
all six. Visual inspection still shows broad rainbow bands and noisy areas.
The metric can also fall when real color detail is softened. A clean reference
is unavailable, so a general image-quality gain is not established.

The earlier statistical profiles contain measured chroma-noise and block-seam statistics, sample
counts, and the signal type. They are not neural weights. One short temporal
export with each profile decoded and produced an MP4. The damaged Analog
source yielded only 0.6 seconds of decodable frames from a nominal one-second
span in both the plain and temporal paths; this is a source limitation. A
clean reference for these flights is unavailable, so restoration quality has
not yet been objectively established. Keep these local profiles out of a
public release until footage rights and quality have been reviewed.
