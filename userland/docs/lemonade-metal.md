# OnePlus 9 Metal acceptance

The 2026-10-24 work runs Apple-compiled iOS AIR on the SM8350's Adreno 660
through this project's Metal frontend and Mesa Freedreno. It is experimental;
selecting the Apple2 capability table does not establish Metal family compliance.

The kernel repository's `docs/LEMONADE-DISPLAY.md` records matching test runs,
pixel results, presentation timing, build instructions, and remaining limits.
Private system images, SDKs, Apple shader payloads, device records, and logs are
kept outside Git. The test shaders in `lemonade_metal_test.metal` are our own.

## Accepted behavior

- Viewport/scissor, winding/culling, wireframe, clipping/clamping, depth bias,
  per-draw blend constants, clear-only passes, and retained encoded resources.
- GPU buffer fill/copy, texture region copy, buffer/texture row-stride copies,
  mipmap generation, and two/four-sample color resolve including edge coverage.
- RGBA/BGRA blending and framebuffer fetch, R8/RGBA16Float sampling, Stencil8
  writes and sampling, semantic varying linkage, and first-vertex flat shading.
- Float/half derivatives, analytic corner coverage, half varying transport,
  typed image stores, and CPU/GPU shared IOSurface ownership.
- System device discovery, two public MPS Gaussian blur cases, and four public
  CARenderer corner/clipping cases.

`lemonade_metal_features.m` runs the same 88 pixel/buffer checks on Apple M4
and the phone using the same iOS metallib. Native compilation defines
`LEMONADE_NATIVE_MAIN` and links Foundation, Metal, and IOSurface. The separate
`lemonade_metal_ca.m` and `lemonade_metal_mps.m` programs check framework use.
The older raster suite additionally covers 15 viewport/state/lifetime cases.
Individual successful checks establish their tested cases, not all inputs.

## Correctness and performance decisions

All AIR/Gallium entry points share a recursive mutex. A command buffer holds
that mutex across its recorded work and pays the GPU fence before publishing
completion or delivering completed handlers. GPU draws can remain in the same
batch; resource-write/transfer ordering retains explicit barriers. Surface and
sampler-view references outlive deferred Gallium work. Commit remains synchronous.

AIR fragment derivatives and implicit-LOD texture operations request quad helper
invocations. Missing neighbours at triangle edges caused visible corner cracks.
Adreno's derivative instruction uses 32-bit operands, so half values are promoted
at that instruction and rounded back afterward. Half varyings likewise use
32-bit stage-interface slots while internal shader calculations keep their types.

After function specialization, `uniform_extents` performs conservative constant
folding and records surviving UBO loads. Constant offsets bound the upload;
dynamic offsets retain the full range, and eliminated loads need no upload.
Unsized-array tests use an offset valid for Metal but unaligned for Adreno and
read both static index 3 and runtime index 127. This avoids copying hundreds of
kilobytes of a ring buffer per draw without truncating dynamic accesses.

At the unchanged default 315 MHz, the fixed 11-action desktop workload improved
from 13.78 to 54.68 FPS while the finger was down. All 88 + 2 + 4 checks passed.
These are framebuffer completion intervals; long frames remain and this does not
prove tear-free panel scanning or sustained 60 FPS. The separately checked
443 MHz run reached 55.92 FPS, so the default clock remains unchanged.

A five-minute run at 315 MHz included 192 seconds of continuous scrolling:
58.32 FPS, P95 interval 30.729 ms, maximum 35.575 ms. All 88 + 2 + 4 pixel checks
passed again afterward. Across 12,780 presentations, the display driver recorded
no copy-window overrun, explicit drop, or unexpected display IRQ. GPU sensors
peaked at 57.6 / 56.9 Celsius. This is bounded acceptance, not a thermal policy or
a 30-minute endurance result.

The final process-local regression also passed FD660 GLES readback, 26 IOSurface
checks, all 15 raster cases, and all 88 feature checks. The two deliberately invalid
index draws were rejected as expected. Both original root and System images kept
their verified hashes; all graphics overlays run in RAM.

## Remaining limits

Full texture views, heaps/events, nonuniform threadgroups, depth resolve, every
private SPI, and all applications are not implemented or comprehensively tested.
The generic sample-count capability stays at one pending wider format/resolve
coverage, despite the explicit two/four-sample color tests. Display output still
uses CPU scaling/copying into the firmware framebuffer. Direct DPU scanout,
producer release fences, 120 Hz, Wi-Fi/GPU integration, and sustained thermal
policy require separate device validation.
