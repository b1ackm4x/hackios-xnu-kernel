/* Independent oracle: the same quad through Apple's native macOS Metal driver. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>

int main(void)
{
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        NSError *error = nil;
        NSString *source = @"#include <metal_stdlib>\nusing namespace metal;\n"
            "vertex float4 referenceVertex(uint i [[vertex_id]]) { const float4 p[] = {"
            "float4(-1,-1,0,1),float4(1,-1,0,1),float4(-1,1,0,1),float4(1,1,0,1)};return p[i];}\n"
            "fragment uint4 ink() { return uint4(0x5a5a5a5a); }";
        id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
        if (!device || !library) {
            fprintf(stderr, "native Metal compile failed: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [library newFunctionWithName:@"referenceVertex"];
        pd.fragmentFunction = [library newFunctionWithName:@"ink"];
        pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Uint;
        id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:pd error:&error];
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Uint width:32 height:24 mipmapped:NO];
        td.usage = MTLTextureUsageRenderTarget;
        td.storageMode = MTLStorageModeShared;
        id<MTLTexture> target = [device newTextureWithDescriptor:td];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        if (!pipeline || !target || !queue) {
            fprintf(stderr, "native Metal setup failed: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }
        for (unsigned winding = 0; winding < 2; winding++) for (unsigned cull = 1; cull < 3; cull++) {
            MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture = target;
            pass.colorAttachments[0].loadAction = MTLLoadActionClear;
            pass.colorAttachments[0].storeAction = MTLStoreActionStore;
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
            [encoder setRenderPipelineState:pipeline];
            [encoder setFrontFacingWinding:(MTLWinding)winding];
            [encoder setCullMode:(MTLCullMode)cull];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
            [encoder endEncoding]; [command commit]; [command waitUntilCompleted];
            if (command.status != MTLCommandBufferStatusCompleted) return 1;
            uint32_t pixels[32*24*4];
            [target getBytes:pixels bytesPerRow:32*16 fromRegion:MTLRegionMake2D(0,0,32,24) mipmapLevel:0];
            unsigned drawn = 0;
            for (unsigned i=0; i<32*24; i++) drawn += pixels[i*4] == 0x5a5a5a5a;
            printf("native-metal: winding=%u cull=%u drawn=%u renderer=%s\n", winding, cull, drawn, device.name.UTF8String);
        }
    }
    return 0;
}
