/* Pixel-level raster-state acceptance, run through the actual Metal driver.
 * The oracle is a rectangle in pixel coordinates, independent of the AIR backend.
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { W = 32, H = 24, TARGETS = 3 };
struct raster_case {
    const char *name;
    BOOL viewport, scissor, arrays;
    MTLViewport vp;
    MTLScissorRect clip;
    MTLCullMode cull;
    MTLWinding winding;
    MTLTriangleFillMode fill;
    BOOL culled;
};

static BOOL covered(unsigned x, unsigned y, const struct raster_case *c)
{
    double px = x + 0.5, py = y + 0.5;
    BOOL in_view = !c->viewport || (px >= c->vp.originX && py >= c->vp.originY &&
        px < c->vp.originX + c->vp.width && py < c->vp.originY + c->vp.height);
    BOOL in_clip = !c->scissor || (x >= c->clip.x && y >= c->clip.y &&
        x - c->clip.x < c->clip.width && y - c->clip.y < c->clip.height);
    return in_view && in_clip && !c->culled;
}

static void encode(id<MTLRenderCommandEncoder> encoder, id<MTLRenderPipelineState> pipeline,
                   id<MTLBuffer> constant, const struct raster_case *c)
{
    [encoder setRenderPipelineState:pipeline];
    [encoder setCullMode:c->cull];
    [encoder setFrontFacingWinding:c->winding];
    [encoder setTriangleFillMode:c->fill];
    for (NSUInteger i = 0; i < 16; i++)
        [encoder setFragmentBuffer:constant offset:0 atIndex:i];
    if (c->viewport) {
        if (c->arrays) [encoder setViewports:&c->vp count:1];
        else [encoder setViewport:c->vp];
    }
    if (c->scissor) {
        if (c->arrays) [encoder setScissorRects:&c->clip count:1];
        else [encoder setScissorRect:c->clip];
    }
    [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
}

int lemonade_metal_raster(id<MTLDevice> device, const char *path)
{
    @autoreleasepool {
        NSError *error = nil;
        id<MTLLibrary> library = [device newLibraryWithFile:[NSString stringWithUTF8String:path]
                                                   error:&error];
        id<MTLFunction> fragment = [library newFunctionWithName:@"clearFragment20"];
        MTLRenderPipelineDescriptor *pd = [[MTLRenderPipelineDescriptor alloc] init];
        pd.fragmentFunction = fragment;
        for (NSUInteger i = 0; i < TARGETS; i++) pd.colorAttachments[i].pixelFormat = MTLPixelFormatRGBA32Uint;
        id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:pd error:&error];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLBuffer> a = [device newBufferWithLength:256 options:MTLResourceStorageModeShared];
        id<MTLBuffer> b = [device newBufferWithLength:256 options:MTLResourceStorageModeShared];
        id<MTLTexture> targets[TARGETS] = {nil};
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Uint
                                    width:W height:H mipmapped:NO];
        td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        BOOL ready = library && fragment && pipeline && queue && a && b && a.contents && b.contents;
        for (NSUInteger i = 0; i < TARGETS; i++) {
            targets[i] = [device newTextureWithDescriptor:td];
            ready = ready && targets[i] != nil;
        }
        if (!ready) {
            printf("lemonade-metal: raster FAIL setup: %s\n", error ? error.localizedDescription.UTF8String : "missing GPU resource");
            return 1;
        }
        memset(a.contents, 0x5a, 256);
        memset(b.contents, 0x3c, 256);
        const struct raster_case cases[] = {
            {"full-control", NO, NO, NO, {0}, {0}},
            {"viewport", YES, NO, NO, {4, 3, 19, 13, 0, 1}, {0}},
            {"scissor", NO, YES, NO, {0}, {7, 5, 13, 11}},
            {"intersection", YES, YES, NO, {4, 3, 19, 13, 0, 1}, {7, 5, 21, 17}},
            {"empty-scissor", NO, YES, NO, {0}, {7, 5, 0, 11}},
            {"fractional-viewport", YES, NO, NO, {4.25, 2.75, 18.5, 13.5, 0, 1}, {0}},
            {"negative-origin", YES, NO, NO, {-4, -3, 19, 13, 0, 1}, {0}},
            {"single-array-state", YES, YES, YES, {3, 2, 17, 19, 0, 1}, {5, 7, 13, 11}},
            {.name="cull-back-ccw", .cull=MTLCullModeBack, .winding=MTLWindingCounterClockwise},
            {.name="cull-front-ccw", .cull=MTLCullModeFront, .winding=MTLWindingCounterClockwise, .culled=YES},
            {.name="cull-back-cw", .cull=MTLCullModeBack, .winding=MTLWindingClockwise, .culled=YES},
            {.name="cull-front-cw", .cull=MTLCullModeFront, .winding=MTLWindingClockwise},
            {.name="wireframe", .fill=MTLTriangleFillModeLines},
        };
        unsigned failed = 0, count = sizeof(cases) / sizeof(cases[0]);
        for (unsigned i = 0; i < count + 2; i++) {
            BOOL sequence = i == count, lifetime = i == count + 1;
            const struct raster_case *c = &cases[sequence ? 1 : lifetime ? 0 : i];
            const char *name = sequence ? "two-draw-state-and-load-action" :
                               lifetime ? "release-resources-before-commit" : c->name;
            MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
            for (NSUInteger t = 0; t < TARGETS; t++) {
                pass.colorAttachments[t].texture = targets[t];
                pass.colorAttachments[t].loadAction = MTLLoadActionClear;
                pass.colorAttachments[t].storeAction = MTLStoreActionStore;
                pass.colorAttachments[t].clearColor = MTLClearColorMake(0, 0, 0, 0);
            }
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
            if (sequence) encode(encoder, pipeline, a, &cases[0]);
            id<MTLRenderPipelineState> transientPipeline = nil;
            id<MTLBuffer> transientBuffer = nil;
            if (lifetime) {
                transientPipeline = [device newRenderPipelineStateWithDescriptor:pd error:&error];
                transientBuffer = [device newBufferWithLength:256 options:MTLResourceStorageModeShared];
                if (!transientPipeline || !transientBuffer.contents) {
                    printf("lemonade-metal: raster %s FAIL setup\n", name);
                    failed++;
                    [encoder endEncoding];
                    [transientPipeline release]; [transientBuffer release];
                    continue;
                }
                memset(transientBuffer.contents, 0x5a, 256);
            }
            encode(encoder, lifetime ? transientPipeline : pipeline,
                   lifetime ? transientBuffer : sequence ? b : a, c);
            [encoder endEncoding];
            [transientPipeline release]; [transientBuffer release];
            [command commit];
            [command waitUntilCompleted];
            uint32_t pixels[W * H * 4];
            memset(pixels, 0xcd, sizeof(pixels));
            [targets[2] getBytes:pixels bytesPerRow:W * 16 fromRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0];
            unsigned different = 0, drawn = 0, expected_drawn = 0;
            for (unsigned y = 0; y < H; y++) for (unsigned x = 0; x < W; x++) {
                BOOL inside = covered(x, y, c);
                uint32_t expected = inside ? (sequence ? 0x3c3c3c3c : 0x5a5a5a5a) : (sequence ? 0x5a5a5a5a : 0);
                BOOL same = YES;
                if (c->fill == MTLTriangleFillModeLines) {
                    /* A line's endpoint coverage varies by GPU. Require consistent
                     * channels and untouched pixels safely inside either triangle. */
                    uint32_t first = pixels[(y * W + x) * 4];
                    BOOL interior = x > 2 && x < W-3 && y > 2 && y < H-3 &&
                        abs((int)(x * H) - (int)(y * W)) > 3 * W;
                    same = (first == 0 || first == 0x5a5a5a5a) && (!interior || first == 0);
                    for (unsigned n = 1; n < 4; n++) same = same && pixels[(y * W + x) * 4 + n] == first;
                } else {
                    for (unsigned n = 0; n < 4; n++) same = same && pixels[(y * W + x) * 4 + n] == expected;
                }
                different += !same;
                drawn += pixels[(y * W + x) * 4] == (sequence ? 0x3c3c3c3c : 0x5a5a5a5a);
                expected_drawn += inside;
            }
            BOOL pass_ok = encoder && command.status == MTLCommandBufferStatusCompleted && !different;
            if (c->fill == MTLTriangleFillModeLines) pass_ok = pass_ok && drawn > W && drawn < W * H / 2;
            failed += !pass_ok;
            if (c->fill == MTLTriangleFillModeLines)
                printf("lemonade-metal: raster %s %s mismatch_pixels=%u drawn=%u expected_range=33..383 status=%lu\n",
                       name, pass_ok ? "PASS" : "FAIL", different, drawn, (unsigned long)command.status);
            else
                printf("lemonade-metal: raster %s %s mismatch_pixels=%u drawn=%u expected=%u status=%lu\n",
                       name, pass_ok ? "PASS" : "FAIL", different, drawn, expected_drawn, (unsigned long)command.status);
        }
        printf("lemonade-metal: raster summary checks=%u failed=%u\n", count + 2, failed);
        for (NSUInteger i = 0; i < TARGETS; i++) [targets[i] release];
        [a release]; [b release]; [queue release]; [pipeline release]; [pd release];
        [fragment release]; [library release];
        return failed ? 1 : 0;
    }
}
