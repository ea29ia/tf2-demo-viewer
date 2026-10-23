# Heuristics and pitfalls — traps we actually stepped on

- Date: 2026-10-23
- Author: agent session (OpenCode + GLM), x86_64 Linux desktop
- Scope: project-wide debugging heuristics distilled from this session's
  texture-pipeline bug hunt; the concrete bugs are documented in
  `decisions.md` and `40-vpk-mdl-pipeline.md` — this file is about the
  METHOD and the traps in reasoning/verification themselves.
- Tags: #heuristics #debugging #verification #process

## State
This is a checklist of failure patterns we hit while chasing "textures are
wrong" for several sessions. Every entry cost real time. When debugging any
export/render pipeline, read this first.

## Heuristics that worked

1. **Self-consistency beats ground-truth guessing.** When we could not decide
   whether a VTF decode was correct (no reference image), we downscaled the
   decoded mip0 by 2 and compared it with the independently decoded mip1:
   mean|diff| 1.0 = chain is consistent, 41.4 = we are reading the wrong
   bytes. No external reference needed. Look for internal redundancy in the
   format (mip chains, checksums, symmetric bones, mirrored UVs) and use it
   as an oracle.
2. **Compare two implementations that disagree, then bisect the pipeline.**
   The decisive texture-orientation answers came from rendering the SAME
   mesh offline (Python rasterizer) and in a real browser (three.js) and
   diffing. If offline is right and browser is wrong, the bug is in the
   browser layer — do not keep auditing the exporter.
3. **Verify at the layer you are shipping, not the layer you edited.** After
   fixing the converter, render the actual GLB file contents (parse the GLB
   back and rasterize from ITS accessors), not from source VPK data. This
   caught "export verified but GLB differs" cases and also cleared the GLB
   when the bug was elsewhere.
4. **When every axis transform fails, suspect the DATA, not the transform.**
   We brute-forced all 8 UV flips/swaps against skin-rate — all failed.
   That was the signal the texture itself was wrong, not the UVs. If a small
   closed set of hypotheses all fail, generate new hypotheses outside the set.
5. **Numeric sampling beats eyeballing.** "Skin rate" (fraction of verts whose
   UV samples a skin-colored texel) turned subjective "looks wrong" into a
   number that moved 5% → 70% after the fix. Define a cheap proxy metric for
   any visual bug before iterating.
6. **Trust per-vertex normals as ground truth for winding.** Geometric normal
   of a triangle vs its stored vertex normals: if most disagree, winding is
   inverted. This found the CW/CCW bug in minutes after days of texture
   confusion.

## Traps we fell into (do not repeat)

1. **Documenting a conclusion from buggy output.** The 40-note's gotcha said
   "mip 0 is FIRST after the header" — written while the decoder was broken,
   the exact opposite of the truth. Any fact derived from a tool that is
   itself under suspicion must be marked provisional, or re-verified after
   the tool is fixed. Stale wrong docs actively mislead the next agent.
2. **Fixing one real bug, then attributing ALL symptoms to it.** The winding
   bug WAS real and did explain a lot — but the face was still black after
   it, because the texture data was scrambled. One symptom can have several
   stacked causes; re-test after every fix and keep the remaining symptom
   list explicit.
3. **A/B testing with a broken baseline.** We spent a whole round choosing
   between V-flip variants by which looked "less wrong". Both were rendered
   from corrupted texture bytes. If both options look equally wrong, the
   discriminator question is wrong — step back a layer.
4. **Assuming the SDK header layout matches the shipped game's files.** TF2's
   MDL v48 does not match studio.h offsets (numlocalseq is not at 124;
   seqdesc stride is not 212). Crowbar's v48 reader differs from the SDK too.
   When in doubt, locate structures EMPIRICALLY (find known strings, solve
   for offsets), then verify against a second file.
5. **False alarms from diagnostic code.** A "prim1 position mismatch" scare
   came from the diagnostic script reading the shared position accessor
   wrong; a "20% coverage" rasterizer used a broken winding test. Write the
   diagnostic as carefully as the fix — a buggy test wastes a whole round.
6. **Searching for a missing file when the file is present but misread.**
   We hypothesized "wrong texture in another VPK", "separate lo-d atlas",
   "half-atlas scale transform" before checking the simplest thing: is the
   decoder's output internally consistent? Cheapest checks first.
7. **Headless browser ≠ user's browser.** Chromium headless rendered fine
   while the user's Firefox was broken. Environment differences (ImageBitmap
   orientation, GPU paths) mean a headless PASS does not close a visual bug;
   only the user's eyeball on the user's browser does. Budget for one
   user-verification round per visual change.
8. **stop-guessing rule:** after ~3 failed fix attempts on one symptom,
   stop proposing fixes and go gather new evidence (render something,
   print numbers, diff two layers). We violated this repeatedly during the
   texture hunt and burned rounds on plausible-but-unverified hypotheses
   (HWM layouts, atlas scale shifts, browser flip differences).

## Open questions
- None for this file; add new traps here as they are discovered (one bullet,
  with the cost it incurred).
