/* Public Metal acceptance, also executable on Apple hardware as an oracle. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#if __has_include(<IOSurface/IOSurface.h>)
#import <IOSurface/IOSurface.h>
#else
#import <IOSurface/IOSurfaceRef.h>
#endif
#include <dispatch/dispatch.h>
#include <math.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

static unsigned checks, failures;
static void result(const char *name, BOOL ok, NSUInteger status, unsigned mismatches)
{
    checks++; failures += !ok;
    printf("lemonade-metal: feature %s %s status=%lu mismatches=%u\n", name,
           ok ? "PASS" : "FAIL", (unsigned long)status, mismatches);
}
static BOOL finish(id<MTLCommandBuffer> command)
{
    [command commit]; [command waitUntilCompleted];
    if (command.error) printf("lemonade-metal: command error %s\n", command.error.localizedDescription.UTF8String);
    return command.status == MTLCommandBufferStatusCompleted;
}

/* Release the application's ownership after binding, before the first draw or
 * dispatch. A separate autorelease pool also exercises set*Bytes storage. */
static void lifetimeTests(id<MTLDevice> device, id<MTLCommandQueue> queue, id<MTLLibrary> library)
{
    for (unsigned mode=0;mode<3;mode++) @autoreleasepool {
        NSError *error=nil;
        id<MTLFunction> vs=[library newFunctionWithName:@"lemonade_vertex"];
        id<MTLFunction> fs=[library newFunctionWithName:@"lemonade_color"];
        MTLRenderPipelineDescriptor *pd=[[MTLRenderPipelineDescriptor alloc] init];
        pd.vertexFunction=vs;pd.fragmentFunction=fs;
        pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
        pd.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float;
        id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:pd error:&error];
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
        td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> target=[device newTextureWithDescriptor:td],readback=[device newTextureWithDescriptor:td];
        MTLTextureDescriptor *zd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float width:16 height:16 mipmapped:NO];
        zd.usage=MTLTextureUsageRenderTarget;
        id<MTLTexture> depth=[device newTextureWithDescriptor:zd];
        MTLDepthStencilDescriptor *ds=[[MTLDepthStencilDescriptor alloc] init];
        ds.depthCompareFunction=MTLCompareFunctionAlways;ds.depthWriteEnabled=YES;
        id<MTLDepthStencilState> state=[device newDepthStencilStateWithDescriptor:ds];
        float z=.5f,green[4]={0,1,0,1};
        id<MTLBuffer> vb=[device newBufferWithBytes:&z length:sizeof(z) options:0];
        id<MTLBuffer> fb=[device newBufferWithBytes:green length:sizeof(green) options:0];
        const char *name=(const char *[]) {"render-release-before-first-draw","render-bytes-pool-before-first-draw","render-attachment-snapshot-lifetime"}[mode];
        if (!pipeline||!target||!readback||!depth||!state||!vb||!fb) {
            result(name,NO,0,1);
        } else {
            MTLRenderPassDescriptor *pass=[[MTLRenderPassDescriptor alloc] init];
            pass.colorAttachments[0].texture=target;
            pass.colorAttachments[0].loadAction=MTLLoadActionDontCare;
            pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            pass.depthAttachment.texture=depth;
            pass.depthAttachment.loadAction=MTLLoadActionDontCare;
            pass.depthAttachment.storeAction=MTLStoreActionDontCare;
            id<MTLCommandBuffer> command=[queue commandBuffer];
            id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
            [encoder setRenderPipelineState:pipeline];[encoder setDepthStencilState:state];
            if (mode==1) @autoreleasepool {
                [encoder setVertexBytes:&z length:sizeof(z) atIndex:0];
                [encoder setFragmentBytes:green length:sizeof(green) atIndex:0];
            } else {
                [encoder setVertexBuffer:vb offset:0 atIndex:0];[encoder setFragmentBuffer:fb offset:0 atIndex:0];
            }
            [pipeline release];pipeline=nil;[state release];state=nil;
            [vb release];vb=nil;[fb release];fb=nil;
            /* The encoder must snapshot and own attachments even when no load
             * clear command provides an independent reference to the texture. */
            pass.colorAttachments[0].texture=nil;pass.depthAttachment.texture=nil;[pass release];
            id<MTLTexture> recordedTarget=target;
            if(mode==2){[target release];target=nil;[depth release];depth=nil;}
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];
            id<MTLBlitCommandEncoder> blit=[command blitCommandEncoder];
            [blit copyFromTexture:recordedTarget sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
                      sourceSize:MTLSizeMake(16,16,1) toTexture:readback destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
            [blit endEncoding];BOOL done=finish(command);
            uint8_t pixels[1024];[readback getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
            unsigned different=0;for(unsigned p=0;p<256;p++)for(unsigned c=0;c<4;c++)different+=pixels[p*4+c]!=(c==1||c==3?255:0);
            result(name,done&&encoder&&!different,command.status,different);
        }
        [pipeline release];[state release];[vb release];[fb release];
        [target release];[readback release];[depth release];[ds release];[pd release];[vs release];[fs release];
    }
    @autoreleasepool {
        NSError *error=nil;uint32_t count=64;
        id<MTLFunction> function=[library newFunctionWithName:@"lemonade_compute"];
        id<MTLComputePipelineState> pipeline=[device newComputePipelineStateWithFunction:function error:&error];
        id<MTLBuffer> output=[device newBufferWithLength:512 options:MTLResourceStorageModeShared];
        id<MTLBuffer> countBuffer=[device newBufferWithBytes:&count length:sizeof(count) options:0];
        if(pipeline&&output.contents&&countBuffer) {
            memset(output.contents,0xa5,512);
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLComputeCommandEncoder> encoder=[command computeCommandEncoder];
            [encoder setComputePipelineState:pipeline];[encoder setBuffer:output offset:0 atIndex:0];[encoder setBuffer:countBuffer offset:0 atIndex:1];
            [pipeline release];pipeline=nil;[countBuffer release];countBuffer=nil;
            [encoder dispatchThreadgroups:MTLSizeMake(4,1,1) threadsPerThreadgroup:MTLSizeMake(16,1,1)];[encoder endEncoding];
            BOOL done=finish(command);unsigned different=0;
            for(unsigned i=0;i<128;i++)different+=((uint32_t*)output.contents)[i]!=(i<64?i*7+3:0xa5a5a5a5);
            result("compute-release-before-first-dispatch",done&&encoder&&!different,command.status,different);
        } else result("compute-release-before-first-dispatch",NO,0,1);
        [pipeline release];[countBuffer release];[output release];[function release];
    }
    @autoreleasepool {
        NSError *error=nil;
        id<MTLFunction> function=[library newFunctionWithName:@"lemonade_image_sample"];
        id<MTLComputePipelineState> pipeline=[device newComputePipelineStateWithFunction:function error:&error];
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:17 height:17 mipmapped:NO];
        td.usage=MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite;
        id<MTLTexture> source=[device newTextureWithDescriptor:td],target=[device newTextureWithDescriptor:td];
        MTLSamplerDescriptor *sd=[[MTLSamplerDescriptor alloc] init];sd.normalizedCoordinates=NO;
        sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;sd.sAddressMode=sd.tAddressMode=MTLSamplerAddressModeClampToEdge;
        id<MTLSamplerState> sampler=[device newSamplerStateWithDescriptor:sd];
        if(pipeline&&source&&target&&sampler) {
            uint8_t input[17*17*4],pixels[sizeof(input)];for(unsigned i=0;i<sizeof(input);i++)input[i]=(i*37+13)&255;
            [source replaceRegion:MTLRegionMake2D(0,0,17,17) mipmapLevel:0 withBytes:input bytesPerRow:68];
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLComputeCommandEncoder> encoder=[command computeCommandEncoder];
            [encoder setComputePipelineState:pipeline];[encoder setTexture:source atIndex:1];[encoder setTexture:target atIndex:0];[encoder setSamplerState:sampler atIndex:0];
            [pipeline release];pipeline=nil;[source release];source=nil;[sampler release];sampler=nil;
            [encoder dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(32,32,1)];[encoder endEncoding];BOOL done=finish(command);
            [target getBytes:pixels bytesPerRow:68 fromRegion:MTLRegionMake2D(0,0,17,17) mipmapLevel:0];unsigned different=0;
            for(unsigned i=0;i<sizeof(input);i++)different+=abs((int)pixels[i]-input[i])>1;
            result("compute-texture-sampler-release-before-dispatch",done&&encoder&&!different,command.status,different);
        } else result("compute-texture-sampler-release-before-dispatch",NO,0,1);
        [pipeline release];[source release];[target release];[sampler release];[sd release];[function release];
    }
}

int lemonade_metal_features(id<MTLDevice> device, const char *path)
{
    @autoreleasepool {
        checks = failures = 0;
        NSError *error = nil;
        id<MTLLibrary> library = [device newLibraryWithFile:[NSString stringWithUTF8String:path] error:&error];
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLFunction> vertex = [library newFunctionWithName:@"lemonade_vertex"];
        id<MTLFunction> fragment = [library newFunctionWithName:@"lemonade_color"];
        MTLRenderPipelineDescriptor *pd = [[MTLRenderPipelineDescriptor alloc] init];
        pd.vertexFunction = vertex; pd.fragmentFunction = fragment;
        pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
        pd.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
        id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:pd error:&error];
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                                     width:16 height:16 mipmapped:NO];
        td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        id<MTLTexture> color = [device newTextureWithDescriptor:td];
        td.pixelFormat = MTLPixelFormatDepth32Float; td.storageMode = MTLStorageModePrivate;
        id<MTLTexture> depth = [device newTextureWithDescriptor:td];
        id<MTLBuffer> z = [device newBufferWithLength:256 options:MTLResourceStorageModeShared];
        id<MTLBuffer> rgba = [device newBufferWithLength:256 options:MTLResourceStorageModeShared];
        if (!pipeline || !color || !depth || !queue || !z.contents || !rgba.contents) {
            printf("lemonade-metal: feature setup FAIL %s\n", error ? error.localizedDescription.UTF8String : "resource allocation");
            return 1;
        }
        const float green[] = {0, 1, 0, 1}; memcpy(rgba.contents, green, sizeof(green));
        struct depth_case { const char *name; float z, clear, bias, clamp; MTLDepthClipMode clip; MTLCompareFunction compare; BOOL drawn; };
        const struct depth_case cases[] = {
            {"depth-inside", .5f, .6f, 0, 0, MTLDepthClipModeClip, MTLCompareFunctionLess, YES},
            {"depth-clip-near", -.25f, .6f, 0, 0, MTLDepthClipModeClip, MTLCompareFunctionLess, NO},
            {"depth-clip-far", 1.25f, .8f, 0, 0, MTLDepthClipModeClip, MTLCompareFunctionGreater, NO},
            {"depth-clamp-near", -.25f, .6f, 0, 0, MTLDepthClipModeClamp, MTLCompareFunctionLess, YES},
            {"depth-clamp-far", 1.25f, .8f, 0, 0, MTLDepthClipModeClamp, MTLCompareFunctionGreater, YES},
            {"depth-bias-positive", .5f, .50001f, 10000, 0, MTLDepthClipModeClip, MTLCompareFunctionLess, NO},
            {"depth-bias-negative", .5f, .49999f, -10000, 0, MTLDepthClipModeClip, MTLCompareFunctionLess, YES},
            {"depth-bias-clamped", .5f, .50001f, 10000, .000001f, MTLDepthClipModeClip, MTLCompareFunctionLess, YES},
        };
        for (unsigned i = 0; i < sizeof(cases)/sizeof(cases[0]); i++) {
            const struct depth_case *c = &cases[i]; memcpy(z.contents, &c->z, sizeof(c->z));
            MTLDepthStencilDescriptor *ds = [[MTLDepthStencilDescriptor alloc] init];
            ds.depthCompareFunction = c->compare; ds.depthWriteEnabled = YES;
            id<MTLDepthStencilState> state = [device newDepthStencilStateWithDescriptor:ds];
            MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture = color; pass.colorAttachments[0].loadAction = MTLLoadActionClear;
            pass.colorAttachments[0].storeAction = MTLStoreActionStore;
            pass.colorAttachments[0].clearColor = MTLClearColorMake(1, 0, 0, 1);
            pass.depthAttachment.texture = depth; pass.depthAttachment.loadAction = MTLLoadActionClear;
            pass.depthAttachment.storeAction = MTLStoreActionStore; pass.depthAttachment.clearDepth = c->clear;
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
            [encoder setRenderPipelineState:pipeline]; [encoder setDepthStencilState:state];
            [encoder setDepthClipMode:c->clip]; [encoder setDepthBias:c->bias slopeScale:0 clamp:c->clamp];
            [encoder setVertexBuffer:z offset:0 atIndex:0]; [encoder setFragmentBuffer:rgba offset:0 atIndex:0];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4]; [encoder endEncoding];
            BOOL done = finish(command); uint8_t pixels[16*16*4]; memset(pixels, 0xcd, sizeof(pixels));
            [color getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0, 0, 16, 16) mipmapLevel:0];
            unsigned different = 0;
            for (unsigned p = 0; p < 256; p++) different += pixels[p*4] != (c->drawn ? 0 : 255) ||
                pixels[p*4+1] != (c->drawn ? 255 : 0) || pixels[p*4+2] != 0 || pixels[p*4+3] != 255;
            result(c->name, done && encoder && !different, command.status, different);
            [state release]; [ds release];
        }
        /* Clip-space rules apply even when the pass has no depth attachment. */
        for(unsigned i=0;i<5;i++) @autoreleasepool {
            float value=(float[]){.5f,-.25f,1.25f,-.25f,1.25f}[i];memcpy(z.contents,&value,sizeof(value));
            MTLRenderPipelineDescriptor *p=[[MTLRenderPipelineDescriptor alloc] init];
            p.vertexFunction=vertex;p.fragmentFunction=fragment;p.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
            id<MTLRenderPipelineState> noDepth=[device newRenderPipelineStateWithDescriptor:p error:&error];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=color;
            pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
            [encoder setRenderPipelineState:noDepth];[encoder setVertexBuffer:z offset:0 atIndex:0];[encoder setFragmentBuffer:rgba offset:0 atIndex:0];
            [encoder setDepthClipMode:i>=3?MTLDepthClipModeClamp:MTLDepthClipModeClip];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];BOOL done=finish(command);
            uint8_t pixels[1024];[color getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];unsigned different=0;BOOL drawn=i==0||i>=3;
            for(unsigned j=0;j<256;j++)different+=pixels[j*4]!=(drawn?0:255)||pixels[j*4+1]!=(drawn?255:0)||pixels[j*4+2]!=0||pixels[j*4+3]!=255;
            result((const char *[]) {"no-depth-clip-inside","no-depth-clip-near","no-depth-clip-far","no-depth-clamp-near","no-depth-clamp-far"}[i],done&&noDepth&&!different,command.status,different);
            [noDepth release];[p release];
        }
        for(unsigned mode=0;mode<4;mode++) @autoreleasepool {
            float value=.5f;memcpy(z.contents,&value,sizeof(value));
            MTLRenderPipelineDescriptor *p=[[MTLRenderPipelineDescriptor alloc] init];p.vertexFunction=vertex;p.fragmentFunction=fragment;
            MTLRenderPipelineColorAttachmentDescriptor *a=p.colorAttachments[0];a.pixelFormat=MTLPixelFormatRGBA8Unorm;a.blendingEnabled=YES;
            a.sourceRGBBlendFactor=a.sourceAlphaBlendFactor=(MTLBlendFactor)(11+mode);
            a.destinationRGBBlendFactor=a.destinationAlphaBlendFactor=(MTLBlendFactor)(11+(mode^1));
            id<MTLRenderPipelineState> blendPipeline=[device newRenderPipelineStateWithDescriptor:p error:&error];
            const char *name=(const char *[]) {"blend-constant-color","blend-constant-inverse-color","blend-constant-alpha","blend-constant-inverse-alpha"}[mode];
            if(!blendPipeline){result(name,NO,0,1);[p release];continue;}
            const float clear[]={.2,.4,.6,.8},constants[3][4]={{0,0,0,0},{.25,.5,.75,.625},{.75,.25,.5,.125}};
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=color;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(clear[0],clear[1],clear[2],clear[3]);
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];[encoder setRenderPipelineState:blendPipeline];[encoder setVertexBuffer:z offset:0 atIndex:0];[encoder setFragmentBuffer:rgba offset:0 atIndex:0];
            for(unsigned part=0;part<3;part++) {
                if(part)[encoder setBlendColorRed:constants[part][0] green:constants[part][1] blue:constants[part][2] alpha:constants[part][3]];
                [encoder setScissorRect:(MTLScissorRect){part*5,0,part==2?6:5,16}];[encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
            }
            [encoder endEncoding];BOOL done=finish(command);uint8_t pixels[1024];[color getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];unsigned different=0;
            for(unsigned y=0;y<16;y++)for(unsigned x=0;x<16;x++)for(unsigned c=0;c<4;c++) {
                unsigned part=x<5?0:x<10?1:2;float factor=constants[part][mode<2?c:3];if(mode&1)factor=1-factor;
                unsigned want=lroundf((green[c]*factor+clear[c]*(1-factor))*255);different+=abs((int)pixels[(y*16+x)*4+c]-(int)want)>2;
            }
            result(name,done&&!different,command.status,different);[blendPipeline release];[p release];
        }
        id<MTLFunction> kernel = [library newFunctionWithName:@"lemonade_compute"];
        id<MTLComputePipelineState> compute = [device newComputePipelineStateWithFunction:kernel error:&error];
        id<MTLBuffer> output = [device newBufferWithLength:512 options:MTLResourceStorageModeShared];
        id<MTLBuffer> countBuffer = [device newBufferWithLength:256 options:MTLResourceStorageModeShared];
        if (compute && output.contents && countBuffer.contents) {
            memset(output.contents, 0xa5, 512); *(uint32_t *)countBuffer.contents = 64;
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder];
            [encoder setComputePipelineState:compute]; [encoder setBuffer:output offset:0 atIndex:0];
            [encoder setBuffer:countBuffer offset:0 atIndex:1];
            [encoder dispatchThreadgroups:MTLSizeMake(4,1,1) threadsPerThreadgroup:MTLSizeMake(16,1,1)];
            [encoder endEncoding]; BOOL done = finish(command); unsigned different = 0;
            for (unsigned i = 0; i < 128; i++) different += ((uint32_t *)output.contents)[i] != (i < 64 ? i*7+3 : 0xa5a5a5a5);
            result("compute-bounds-and-results", done && encoder && !different, command.status, different);
        } else result("compute-setup", NO, 0, 1);

        /* Copy commands must be deferred until commit, preserve untouched bytes,
         * and observe earlier commands in the same command buffer. */
        id<MTLBuffer> src = [device newBufferWithLength:1024 options:MTLResourceStorageModeShared];
        id<MTLBuffer> dst = [device newBufferWithLength:1024 options:MTLResourceStorageModeShared];
        if (src.contents && dst.contents) {
            memset(src.contents, 0x31, 1024); memset(dst.contents, 0xa5, 1024);
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLBlitCommandEncoder> encoder = [command blitCommandEncoder];
            [encoder fillBuffer:src range:NSMakeRange(17, 397) value:0x6c];
            [encoder copyFromBuffer:src sourceOffset:0 toBuffer:dst destinationOffset:64 size:512];
            [encoder endEncoding]; BOOL deferred = ((uint8_t *)src.contents)[17] == 0x31 && ((uint8_t *)dst.contents)[64] == 0xa5;
            BOOL done = finish(command); unsigned different = 0;
            for (unsigned i = 0; i < 1024; i++) {
                uint8_t expected = i < 64 || i >= 576 ? 0xa5 : i >= 81 && i < 478 ? 0x6c : 0x31;
                different += ((uint8_t *)dst.contents)[i] != expected;
            }
            result("blit-fill-copy-deferred-order", done && encoder && deferred && !different, command.status, different);
        } else result("blit-buffer-setup", NO, 0, 1);
        td.pixelFormat = MTLPixelFormatRGBA8Unorm; td.storageMode = MTLStorageModeShared;
        td.width = 32; td.height = 16;
        id<MTLTexture> sourceTexture = [device newTextureWithDescriptor:td];
        td.width = 24; td.height = 20;
        id<MTLTexture> destinationTexture = [device newTextureWithDescriptor:td];
        if (sourceTexture && destinationTexture) {
            uint8_t sourcePixels[32*16*4], destinationPixels[24*20*4];
            for (unsigned y = 0; y < 16; y++) for (unsigned x = 0; x < 32; x++)
                for (unsigned c = 0; c < 4; c++) sourcePixels[(y*32+x)*4+c] = (x*11+y*17+c*37) & 255;
            memset(destinationPixels, 0xa5, sizeof(destinationPixels));
            [sourceTexture replaceRegion:MTLRegionMake2D(0,0,32,16) mipmapLevel:0 withBytes:sourcePixels bytesPerRow:128];
            [destinationTexture replaceRegion:MTLRegionMake2D(0,0,24,20) mipmapLevel:0 withBytes:destinationPixels bytesPerRow:96];
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLBlitCommandEncoder> encoder = [command blitCommandEncoder];
            [encoder copyFromTexture:sourceTexture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(3,5,0)
                sourceSize:MTLSizeMake(9,7,1) toTexture:destinationTexture destinationSlice:0 destinationLevel:0
                destinationOrigin:MTLOriginMake(7,4,0)];
            [encoder endEncoding]; [sourceTexture release]; sourceTexture = nil;
            BOOL done = finish(command);
            [destinationTexture getBytes:destinationPixels bytesPerRow:96 fromRegion:MTLRegionMake2D(0,0,24,20) mipmapLevel:0];
            unsigned different = 0;
            for (unsigned y = 0; y < 20; y++) for (unsigned x = 0; x < 24; x++) for (unsigned c = 0; c < 4; c++) {
                uint8_t expected = x >= 7 && x < 16 && y >= 4 && y < 11 ? sourcePixels[((y+1)*32+x-4)*4+c] : 0xa5;
                different += destinationPixels[(y*24+x)*4+c] != expected;
            }
            result("blit-texture-region-and-lifetime", done && encoder && !different, command.status, different);
        } else result("blit-texture-setup", NO, 0, 1);
        MTLTextureDescriptor *md = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                                       width:16 height:16 mipmapped:YES];
        md.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget;
        id<MTLTexture> mip = [device newTextureWithDescriptor:md];
        if (mip) {
            uint8_t pixels[16*16*4]; const uint8_t expected[] = {17,85,153,255};
            for (unsigned i = 0; i < sizeof(pixels); i++) pixels[i] = expected[i%4];
            [mip replaceRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0 withBytes:pixels bytesPerRow:64];
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLBlitCommandEncoder> encoder = [command blitCommandEncoder];
            [encoder generateMipmapsForTexture:mip]; [encoder endEncoding];
            BOOL done = finish(command); unsigned different = 0;
            for (unsigned level = 1; level < 5; level++) {
                unsigned dimension = 16 >> level; memset(pixels, 0xcd, sizeof(pixels));
                [mip getBytes:pixels bytesPerRow:dimension*4 fromRegion:MTLRegionMake2D(0,0,dimension,dimension) mipmapLevel:level];
                for (unsigned i = 0; i < dimension*dimension*4; i++) different += abs((int)pixels[i] - expected[i%4]) > 1;
            }
            result("blit-generate-all-mip-levels", done && encoder && !different, command.status, different);
        } else result("blit-mipmap-setup", NO, 0, 1);
        id<MTLBuffer> upload = [device newBufferWithLength:4096 options:MTLResourceStorageModeShared];
        id<MTLBuffer> download = [device newBufferWithLength:4096 options:MTLResourceStorageModeShared];
        if (upload.contents && download.contents && destinationTexture) {
            for (unsigned i = 0; i < 4096; i++) ((uint8_t *)upload.contents)[i] = (i*13+i/256*7)&255;
            memset(download.contents, 0xa5, 4096);
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLBlitCommandEncoder> encoder = [command blitCommandEncoder];
            [encoder copyFromBuffer:upload sourceOffset:256 sourceBytesPerRow:256 sourceBytesPerImage:0
                sourceSize:MTLSizeMake(13,7,1) toTexture:destinationTexture destinationSlice:0 destinationLevel:0
                destinationOrigin:MTLOriginMake(2,4,0)];
            [encoder copyFromTexture:destinationTexture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(2,4,0)
                sourceSize:MTLSizeMake(13,7,1) toBuffer:download destinationOffset:512 destinationBytesPerRow:256
                destinationBytesPerImage:0];
            [encoder endEncoding]; BOOL done = finish(command); unsigned different = 0;
            for (unsigned i = 0; i < 4096; i++) {
                BOOL copied = i >= 512 && i < 512+7*256 && (i-512)%256 < 52;
                uint8_t expected = copied ? ((uint8_t *)upload.contents)[i-256] : 0xa5;
                different += ((uint8_t *)download.contents)[i] != expected;
            }
            result("blit-buffer-texture-strided-roundtrip", done && encoder && !different, command.status, different);
        } else result("blit-transfer-setup", NO, 0, 1);
        [upload release]; [download release];
        [sourceTexture release]; [destinationTexture release]; [mip release];
        /* A half-pixel viewport edge must resolve to half red/half green; a
         * single-sample copy or merely storing the multisample attachment fails. */
        for (unsigned samples=2; samples<=4; samples*=2) for (unsigned edge=0; edge<2; edge++) {
            MTLRenderPipelineDescriptor *mpd = [[MTLRenderPipelineDescriptor alloc] init];
            mpd.vertexFunction=vertex; mpd.fragmentFunction=fragment; mpd.sampleCount=samples;
            mpd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
            id<MTLRenderPipelineState> msPipeline=[device newRenderPipelineStateWithDescriptor:mpd error:&error];
            MTLTextureDescriptor *mtd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
            mtd.textureType=MTLTextureType2DMultisample; mtd.sampleCount=samples;
            mtd.storageMode=MTLStorageModePrivate; mtd.usage=MTLTextureUsageRenderTarget;
            id<MTLTexture> multisample=[device newTextureWithDescriptor:mtd];
            char name[64]; snprintf(name,sizeof(name),"msaa-%ux-%s-resolve",samples,edge?"half-edge":"full");
            if (!msPipeline || !multisample) { result(name,NO,0,1); }
            else {
                uint8_t sentinel[16*16*4]; memset(sentinel,0xa5,sizeof(sentinel));
                [color replaceRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0 withBytes:sentinel bytesPerRow:64];
                float half=.5f; memcpy(z.contents,&half,sizeof(half));
                MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
                pass.colorAttachments[0].texture=multisample;
                pass.colorAttachments[0].resolveTexture=color;
                pass.colorAttachments[0].loadAction=MTLLoadActionClear;
                pass.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
                pass.colorAttachments[0].storeAction=edge?MTLStoreActionStoreAndMultisampleResolve:MTLStoreActionMultisampleResolve;
                id<MTLCommandBuffer> command=[queue commandBuffer];
                id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
                [encoder setRenderPipelineState:msPipeline];
                [encoder setVertexBuffer:z offset:0 atIndex:0];
                [encoder setFragmentBuffer:rgba offset:0 atIndex:0];
                if (edge) [encoder setViewport:(MTLViewport){0,0,7.5,16,0,1}];
                [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
                [encoder endEncoding]; BOOL done=finish(command);
                uint8_t pixels[16*16*4];
                [color getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
                unsigned different=0;
                for (unsigned y=0;y<16;y++) for (unsigned x=0;x<16;x++) {
                    unsigned red=!edge||x<7?0:x==7?128:255;
                    unsigned green=!edge||x<7?255:x==7?128:0;
                    different+=abs((int)pixels[(y*16+x)*4]-(int)red)>1;
                    different+=abs((int)pixels[(y*16+x)*4+1]-(int)green)>1;
                    different+=pixels[(y*16+x)*4+2]!=0;
                    different+=pixels[(y*16+x)*4+3]!=255;
                }
                result(name,done&&!different,command.status,different);
            }
            [multisample release];[msPipeline release];[mpd release];
        }
        /* A partial 17x17 image in a 32x32 group exercises two-dimensional
         * global IDs and both float and half image reads/writes, as MPS does. */
        for (unsigned mode=0;mode<6;mode++) {
            NSString *functionNames[] = {@"lemonade_image_float",@"lemonade_image_half",@"lemonade_image_params",@"lemonade_image_sample",@"lemonade_image_constant",@"lemonade_image_fixed"};
            const char *caseNames[] = {"compute-image-float-2d","compute-image-half-2d","compute-image-packed16-params","compute-image-pixel-sampler","compute-image-constant-fixed-bounds","compute-image-read-fixed-bounds"};
            id<MTLFunction> imageKernel=[library newFunctionWithName:functionNames[mode]];
            id<MTLComputePipelineState> imagePipeline=[device newComputePipelineStateWithFunction:imageKernel error:&error];
            MTLTextureDescriptor *imageTD=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:17 height:17 mipmapped:NO];
            imageTD.usage=MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite;
            id<MTLTexture> inputImage=[device newTextureWithDescriptor:imageTD],outputImage=[device newTextureWithDescriptor:imageTD];
            uint8_t input[17*17*4],outputPixels[sizeof(input)];
            for(unsigned i=0;i<sizeof(input);i++)input[i]=(uint8_t)(i*37+13);
            memset(outputPixels,0xa5,sizeof(outputPixels));
            [inputImage replaceRegion:MTLRegionMake2D(0,0,17,17) mipmapLevel:0 withBytes:input bytesPerRow:68];
            [outputImage replaceRegion:MTLRegionMake2D(0,0,17,17) mipmapLevel:0 withBytes:outputPixels bytesPerRow:68];
            id<MTLCommandBuffer> command=[queue commandBuffer];
            id<MTLComputeCommandEncoder> encoder=[command computeCommandEncoder];
            [encoder setComputePipelineState:imagePipeline];[encoder setTexture:inputImage atIndex:mode>=2?1:0];[encoder setTexture:outputImage atIndex:mode>=2?0:1];
            uint16_t dimensions[4]={17,17,0,0};id<MTLBuffer> params=[device newBufferWithBytes:dimensions length:sizeof(dimensions) options:MTLResourceStorageModeShared];
            if(mode==2)[encoder setBuffer:params offset:0 atIndex:0];
            MTLSamplerDescriptor *sd=[[MTLSamplerDescriptor alloc] init];sd.normalizedCoordinates=NO;sd.minFilter=MTLSamplerMinMagFilterLinear;sd.magFilter=MTLSamplerMinMagFilterLinear;
            sd.sAddressMode=sd.tAddressMode=MTLSamplerAddressModeClampToEdge;
            id<MTLSamplerState> imageSampler=[device newSamplerStateWithDescriptor:sd];
            if(mode==3)[encoder setSamplerState:imageSampler atIndex:0];
            [encoder dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(32,32,1)];[encoder endEncoding];
            BOOL done=finish(command);[outputImage getBytes:outputPixels bytesPerRow:68 fromRegion:MTLRegionMake2D(0,0,17,17) mipmapLevel:0];
            unsigned different=0;const uint8_t solid[4]={64,128,191,255};
            for(unsigned i=0;i<sizeof(input);i++)different+=abs((int)(mode==4?solid[i%4]:input[i])-outputPixels[i])>1;
            if(different)printf("lemonade-metal: image-values mode=%u first=%u,%u,%u,%u second=%u,%u,%u,%u center=%u,%u,%u,%u last=%u,%u,%u,%u\n",mode,outputPixels[0],outputPixels[1],outputPixels[2],outputPixels[3],outputPixels[4],outputPixels[5],outputPixels[6],outputPixels[7],outputPixels[576],outputPixels[577],outputPixels[578],outputPixels[579],outputPixels[1152],outputPixels[1153],outputPixels[1154],outputPixels[1155]);
            result(caseNames[mode],done&&imagePipeline&&!different,command.status,different);
            [params release];[imageSampler release];[sd release];
            [inputImage release];[outputImage release];[imagePipeline release];[imageKernel release];
        }

        /* Load actions execute even without draws; each MRT has its own colour. */
        for (unsigned attachmentCount=1;attachmentCount<=2;attachmentCount++) {
            id<MTLTexture> targets[2]={nil,nil};
            MTLTextureDescriptor *clearTD=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
            clearTD.usage=MTLTextureUsageRenderTarget;
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
            uint8_t sentinel[1024];memset(sentinel,0xa5,sizeof(sentinel));
            for (unsigned i=0;i<attachmentCount;i++) {
                targets[i]=[device newTextureWithDescriptor:clearTD];
                [targets[i] replaceRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0 withBytes:sentinel bytesPerRow:64];
                pass.colorAttachments[i].texture=targets[i];
                pass.colorAttachments[i].loadAction=MTLLoadActionClear;
                pass.colorAttachments[i].storeAction=MTLStoreActionStore;
                pass.colorAttachments[i].clearColor=MTLClearColorMake(i?0:1,i?1:0,0,1);
            }
            id<MTLCommandBuffer> command=[queue commandBuffer];
            id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
            [encoder endEncoding];BOOL done=finish(command);unsigned different=0;
            for (unsigned i=0;i<attachmentCount;i++) {
                uint8_t pixels[1024];memset(pixels,0xcd,sizeof(pixels));
                [targets[i] getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
                for(unsigned p=0;p<256;p++) different+=pixels[p*4]!=(i?0:255)||pixels[p*4+1]!=(i?255:0)||pixels[p*4+2]!=0||pixels[p*4+3]!=255;
                [targets[i] release];
            }
            result(attachmentCount==1?"clear-only-pass":"clear-only-multiple-attachments",done&&encoder&&!different,command.status,different);
        }

        /* Four application threads share one device/context and one pipeline.
         * Their resources and queues remain independent, so any mixed pixels or
         * crash is a driver synchronization failure, not an application race. */
        MTLRenderPipelineDescriptor *parallelPD=[[MTLRenderPipelineDescriptor alloc] init];
        parallelPD.vertexFunction=vertex;parallelPD.fragmentFunction=fragment;
        parallelPD.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
        id<MTLRenderPipelineState> parallelPipeline=[device newRenderPipelineStateWithDescriptor:parallelPD error:&error];
        __block volatile int parallelBad=0;
        if(parallelPipeline) dispatch_apply(4,dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT,0),^(size_t worker) {
            for(unsigned iteration=0;iteration<32;iteration++) @autoreleasepool {
                id<MTLCommandQueue> q=[device newCommandQueue];
                MTLTextureDescriptor *t=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];
                t.usage=MTLTextureUsageRenderTarget;
                id<MTLTexture> target=[device newTextureWithDescriptor:t];
                float zValue=.5f,values[4]={(worker&1)?1:0,(worker&2)?1:0,(iteration&1)?1:0,1};
                id<MTLBuffer> zb=[device newBufferWithBytes:&zValue length:sizeof(zValue) options:MTLResourceStorageModeShared];
                id<MTLBuffer> cb=[device newBufferWithBytes:values length:sizeof(values) options:MTLResourceStorageModeShared];
                MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
                pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;
                pass.colorAttachments[0].storeAction=MTLStoreActionStore;
                id<MTLCommandBuffer> command=[q commandBuffer];
                id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
                [encoder setRenderPipelineState:parallelPipeline];[encoder setVertexBuffer:zb offset:0 atIndex:0];
                [encoder setFragmentBuffer:cb offset:0 atIndex:0];
                [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];
                BOOL done=finish(command);uint8_t pixels[1024];memset(pixels,0xa5,sizeof(pixels));
                [target getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
                unsigned different=0;for(unsigned p=0;p<256;p++)for(unsigned c=0;c<4;c++)different+=pixels[p*4+c]!=(uint8_t)(values[c]*255);
                if(!done||!encoder||different)__sync_fetch_and_add(&parallelBad,1);
                [target release];[zb release];[cb release];[q release];
            }
        }); else parallelBad=1;
        result("parallel-queues-render-readback-128",!parallelBad,0,parallelBad);
        [parallelPipeline release];[parallelPD release];
        /* CoreAnimation uses both fixed-function and programmable blending.
         * Test fetch from each MRT with distinct destination colours and masks. */
        for(unsigned formatIndex=0;formatIndex<2;formatIndex++) for(unsigned kind=0;kind<6;kind++) {
            MTLPixelFormat format=formatIndex?MTLPixelFormatBGRA8Unorm:MTLPixelFormatRGBA8Unorm;
            NSString *names[]={@"lemonade_color",@"lemonade_color",@"lemonade_fetch",@"lemonade_fetch_half",@"lemonade_two",@"lemonade_fetch_two"};
            const char *labels[]={"premultiplied","straight","fetch-float","fetch-half","mrt-mask","mrt-fetch"};
            unsigned count=kind>=4?2:1;
            id<MTLFunction> fs=[library newFunctionWithName:names[kind]];
            MTLRenderPipelineDescriptor *bp=[[MTLRenderPipelineDescriptor alloc] init];bp.vertexFunction=vertex;bp.fragmentFunction=fs;
            for(unsigned i=0;i<count;i++) {
                bp.colorAttachments[i].pixelFormat=format;
                if(kind<2 || (kind==4 && i==0)) {
                    bp.colorAttachments[i].blendingEnabled=YES;
                    bp.colorAttachments[i].sourceRGBBlendFactor=kind==1?MTLBlendFactorSourceAlpha:MTLBlendFactorOne;
                    bp.colorAttachments[i].destinationRGBBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
                    bp.colorAttachments[i].sourceAlphaBlendFactor=MTLBlendFactorOne;
                    bp.colorAttachments[i].destinationAlphaBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
                }
                if(kind==4&&i==1)bp.colorAttachments[i].writeMask=MTLColorWriteMaskBlue|MTLColorWriteMaskAlpha;
            }
            id<MTLRenderPipelineState> blendPipeline=[device newRenderPipelineStateWithDescriptor:bp error:&error];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];id<MTLTexture> targets[2]={nil,nil};
            const float initial[2][4]={{.2,.4,.6,.8},{.8,.6,.4,.2}},source[4]={.25,.125,.5,.5};
            memcpy(rgba.contents,source,sizeof(source));float halfDepth=.5;memcpy(z.contents,&halfDepth,sizeof(halfDepth));
            for(unsigned i=0;i<count;i++) {
                MTLTextureDescriptor *t=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:16 height:16 mipmapped:NO];t.usage=MTLTextureUsageRenderTarget;
                targets[i]=[device newTextureWithDescriptor:t];pass.colorAttachments[i].texture=targets[i];pass.colorAttachments[i].loadAction=MTLLoadActionClear;pass.colorAttachments[i].storeAction=MTLStoreActionStore;
                pass.colorAttachments[i].clearColor=MTLClearColorMake(initial[i][0],initial[i][1],initial[i][2],initial[i][3]);
            }
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
            [encoder setRenderPipelineState:blendPipeline];[encoder setVertexBuffer:z offset:0 atIndex:0];[encoder setFragmentBuffer:rgba offset:0 atIndex:0];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];BOOL done=finish(command);unsigned different=0;
            for(unsigned i=0;i<count;i++) {
                uint8_t pixels[1024];memset(pixels,0xa5,sizeof(pixels));[targets[i] getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
                unsigned expected[4];
                for(unsigned c=0;c<4;c++) {
                    float value=i==0?source[c]*((kind==1&&c<3)?.5f:1.f)+initial[0][c]*.5f:
                        kind==5?initial[1][c]*.5f:c<2?initial[1][c]:(c==2?.3f:.4f);
                    expected[c]=(unsigned)lroundf(value*255);
                }
                for(unsigned pixel=0;pixel<256;pixel++)for(unsigned c=0;c<4;c++) {
                    unsigned channel=formatIndex&&c<3?2-c:c;different+=abs((int)pixels[pixel*4+channel]-(int)expected[c])>2;
                }
                if(different)printf("lemonade-metal: blend-values kind=%u format=%u attachment=%u first=%u,%u,%u,%u expected-rgba=%u,%u,%u,%u\n",kind,formatIndex,i,pixels[0],pixels[1],pixels[2],pixels[3],expected[0],expected[1],expected[2],expected[3]);
                [targets[i] release];
            }
            char label[80];snprintf(label,sizeof(label),"blend-%s-%s",formatIndex?"bgra":"rgba",labels[kind]);
            result(label,done&&blendPipeline&&!different,command.status,different);
            [blendPipeline release];[bp release];[fs release];
        }
        /* Textured compositor quads: interleaved fetch, half varyings, actual
         * sampling, and formats that have different channel/storage layouts. */
        for(unsigned fi=0;fi<4;fi++) for(unsigned halfSample=0;halfSample<2;halfSample++) for(unsigned linear=0;linear<2;linear++) {
            MTLPixelFormat formats[]={MTLPixelFormatRGBA8Unorm,MTLPixelFormatBGRA8Unorm,MTLPixelFormatRGBA16Float,MTLPixelFormatR8Unorm};
            const char *formatNames[]={"rgba8","bgra8","rgba16f","r8"};
            struct sample_vertex { float position[2],uv[2];__fp16 tint[4]; } vertices[]={
                {{-1,-1},{0,1},{.5,.75,.25,.5}},{{1,-1},{1,1},{.5,.75,.25,.5}},
                {{-1,1},{0,0},{.5,.75,.25,.5}},{{1,1},{1,0},{.5,.75,.25,.5}}};
            if(linear)for(unsigned i=0;i<4;i++){vertices[i].uv[0]=vertices[i].uv[0]*1.2f-.1f;vertices[i].uv[1]=vertices[i].uv[1]*.85f+.073f;}
            id<MTLBuffer> vb=[device newBufferWithBytes:vertices length:sizeof(vertices) options:MTLResourceStorageModeShared];
            MTLVertexDescriptor *vd=[[MTLVertexDescriptor alloc] init];
            vd.attributes[0].format=MTLVertexFormatFloat2;vd.attributes[0].offset=0;vd.attributes[0].bufferIndex=1;
            vd.attributes[1].format=MTLVertexFormatFloat2;vd.attributes[1].offset=8;vd.attributes[1].bufferIndex=1;
            vd.attributes[2].format=MTLVertexFormatHalf4;vd.attributes[2].offset=16;vd.attributes[2].bufferIndex=1;
            vd.layouts[1].stride=sizeof(vertices[0]);
            id<MTLFunction> vs=[library newFunctionWithName:@"lemonade_sample_vertex"];
            id<MTLFunction> fs=[library newFunctionWithName:halfSample?@"lemonade_sample_half":@"lemonade_sample_float"];
            MTLRenderPipelineDescriptor *sp=[[MTLRenderPipelineDescriptor alloc] init];sp.vertexFunction=vs;sp.fragmentFunction=fs;sp.vertexDescriptor=vd;sp.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
            id<MTLRenderPipelineState> sampledPipeline=[device newRenderPipelineStateWithDescriptor:sp error:&error];
            MTLTextureDescriptor *st=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:formats[fi] width:16 height:16 mipmapped:NO];st.usage=MTLTextureUsageShaderRead;
            id<MTLTexture> sampled=[device newTextureWithDescriptor:st];
            uint8_t input[2048];float sourceValues[256][4];unsigned bpp=fi==2?8:fi==3?1:4;
            for(unsigned y=0;y<16;y++)for(unsigned x=0;x<16;x++)for(unsigned c=0;c<4;c++) {
                unsigned index=y*16+x;unsigned raw=(x*7+y*11+c*53)%192+32;
                sourceValues[index][c]=fi==3&&c>0?(c==3?1:0):raw/255.f;
                if(fi==2)((__fp16*)input)[index*4+c]=sourceValues[index][c];
                else if(fi==3){if(c==0)input[index]=raw;}
                else input[index*4+(fi==1&&c<3?2-c:c)]=raw;
            }
            [sampled replaceRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0 withBytes:input bytesPerRow:16*bpp];
            MTLSamplerDescriptor *sd=[[MTLSamplerDescriptor alloc] init];sd.minFilter=sd.magFilter=linear?MTLSamplerMinMagFilterLinear:MTLSamplerMinMagFilterNearest;sd.sAddressMode=sd.tAddressMode=MTLSamplerAddressModeClampToEdge;
            id<MTLSamplerState> sampler=[device newSamplerStateWithDescriptor:sd];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=color;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
            [encoder setRenderPipelineState:sampledPipeline];[encoder setVertexBuffer:vb offset:0 atIndex:1];[encoder setFragmentTexture:sampled atIndex:0];[encoder setFragmentSamplerState:sampler atIndex:0];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];BOOL done=finish(command);unsigned different=0;uint8_t pixels[1024];memset(pixels,0xa5,sizeof(pixels));
            [color getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
            const float tint[4]={.5,.75,.25,.5};for(unsigned y=0;y<16;y++)for(unsigned x=0;x<16;x++)for(unsigned c=0;c<4;c++) {
                float expected=sourceValues[y*16+x][c];
                if(linear){float px=(x+.5f)*1.2f-1.6f-.5f,py=(y+.5f)*.85f+.073f*16-.5f;int x0=(int)floorf(px),y0=(int)floorf(py);float tx=px-x0,ty=py-y0;unsigned a=fminf(fmaxf(x0,0),15),b=fminf(fmaxf(x0+1,0),15),d=fminf(fmaxf(y0,0),15),e=fminf(fmaxf(y0+1,0),15);expected=(sourceValues[d*16+a][c]*(1-tx)+sourceValues[d*16+b][c]*tx)*(1-ty)+(sourceValues[e*16+a][c]*(1-tx)+sourceValues[e*16+b][c]*tx)*ty;}
                different+=abs((int)pixels[(y*16+x)*4+c]-(int)lroundf(expected*tint[c]*255))>(linear?2:1);
            }
            if(different)printf("lemonade-metal: sample-values format=%s half=%u linear=%u first=%u,%u,%u,%u center=%u,%u,%u,%u\n",formatNames[fi],halfSample,linear,pixels[0],pixels[1],pixels[2],pixels[3],pixels[544],pixels[545],pixels[546],pixels[547]);
            char name[80];snprintf(name,sizeof(name),"compositor-sample-%s-%s-%s",formatNames[fi],halfSample?"half":"float",linear?"linear":"nearest");result(name,done&&sampledPipeline&&!different,command.status,different);
            [vb release];[vd release];[vs release];[fs release];[sp release];[sampledPipeline release];[sampled release];[sampler release];[sd release];
        }
        /* Stencil creates a mask in one draw and consumes it in the next. Both
         * winding modes must agree when front/back state is the same. */
        for(unsigned winding=0;winding<2;winding++)for(unsigned reference=7;reference<=8;reference++) {
            MTLTextureDescriptor *std=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatStencil8 width:16 height:16 mipmapped:NO];std.storageMode=MTLStorageModePrivate;std.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
            id<MTLTexture> stencil=[device newTextureWithDescriptor:std];
            MTLRenderPipelineDescriptor *spd=[[MTLRenderPipelineDescriptor alloc] init];spd.vertexFunction=vertex;spd.fragmentFunction=fragment;spd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;spd.stencilAttachmentPixelFormat=MTLPixelFormatStencil8;
            id<MTLRenderPipelineState> readPipeline=[device newRenderPipelineStateWithDescriptor:spd error:&error];spd.colorAttachments[0].writeMask=0;
            id<MTLRenderPipelineState> maskPipeline=[device newRenderPipelineStateWithDescriptor:spd error:&error];
            MTLDepthStencilDescriptor *writeDS=[[MTLDepthStencilDescriptor alloc] init];
            MTLStencilDescriptor *writeStencil=[[MTLStencilDescriptor alloc] init];writeStencil.stencilCompareFunction=MTLCompareFunctionAlways;writeStencil.depthStencilPassOperation=MTLStencilOperationReplace;
            writeDS.frontFaceStencil=writeDS.backFaceStencil=writeStencil;id<MTLDepthStencilState> writeState=[device newDepthStencilStateWithDescriptor:writeDS];
            MTLDepthStencilDescriptor *readDS=[[MTLDepthStencilDescriptor alloc] init];MTLStencilDescriptor *readStencil=[[MTLStencilDescriptor alloc] init];readStencil.stencilCompareFunction=MTLCompareFunctionEqual;readStencil.writeMask=0;
            readDS.frontFaceStencil=readDS.backFaceStencil=readStencil;id<MTLDepthStencilState> readState=[device newDepthStencilStateWithDescriptor:readDS];
            float d=.5;memcpy(z.contents,&d,sizeof(d));memcpy(rgba.contents,green,sizeof(green));
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=color;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
            pass.stencilAttachment.texture=stencil;pass.stencilAttachment.loadAction=MTLLoadActionClear;pass.stencilAttachment.clearStencil=0;pass.stencilAttachment.storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
            [encoder setFrontFacingWinding:(MTLWinding)winding];[encoder setVertexBuffer:z offset:0 atIndex:0];[encoder setFragmentBuffer:rgba offset:0 atIndex:0];
            [encoder setRenderPipelineState:maskPipeline];[encoder setDepthStencilState:writeState];[encoder setStencilReferenceValue:7];[encoder setScissorRect:(MTLScissorRect){3,5,7,6}];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
            [encoder setRenderPipelineState:readPipeline];[encoder setDepthStencilState:readState];[encoder setStencilReferenceValue:reference];[encoder setScissorRect:(MTLScissorRect){0,0,16,16}];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];
            id<MTLFunction> sk=[library newFunctionWithName:@"lemonade_stencil_read"];
            id<MTLComputePipelineState> skp=[device newComputePipelineStateWithFunction:sk error:&error];
            id<MTLBuffer> stencilValues=[device newBufferWithLength:1024 options:MTLResourceStorageModeShared];memset(stencilValues.contents,0xa5,1024);
            id<MTLComputeCommandEncoder> readEncoder=[command computeCommandEncoder];[readEncoder setComputePipelineState:skp];[readEncoder setTexture:stencil atIndex:0];[readEncoder setBuffer:stencilValues offset:0 atIndex:0];[readEncoder dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(16,16,1)];[readEncoder endEncoding];
            BOOL done=finish(command);unsigned different=0;uint8_t pixels[1024];
            [color getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
            for(unsigned y=0;y<16;y++)for(unsigned x=0;x<16;x++){BOOL inside=reference==7&&x>=3&&x<10&&y>=5&&y<11;unsigned i=(y*16+x)*4;different+=pixels[i]!=(inside?0:255)||pixels[i+1]!=(inside?255:0)||pixels[i+2]!=0||pixels[i+3]!=255;}
            char name[80];snprintf(name,sizeof(name),"stencil-mask-winding%u-reference%u",winding,reference);result(name,done&&maskPipeline&&readPipeline&&!different,command.status,different);
            unsigned stencilDifferent=0;for(unsigned y=0;y<16;y++)for(unsigned x=0;x<16;x++)stencilDifferent+=((uint32_t*)stencilValues.contents)[y*16+x]!=(x>=3&&x<10&&y>=5&&y<11?7:0);
            snprintf(name,sizeof(name),"stencil-sample-winding%u-reference%u",winding,reference);result(name,done&&skp&&stencil&&!stencilDifferent,command.status,stencilDifferent);
            [sk release];[skp release];[stencilValues release];
            [stencil release];[spd release];[readPipeline release];[maskPipeline release];[writeDS release];[writeStencil release];[writeState release];[readDS release];[readStencil release];[readState release];
        }
        /* Full compositor dimensions cross many Adreno tiles and have a
         * padded, non-power-of-two row pitch. Small tests cannot check that. */
        for(unsigned formatIndex=0;formatIndex<2;formatIndex++) @autoreleasepool {
            const unsigned width=1125,height=2436;
            MTLPixelFormat format=formatIndex?MTLPixelFormatBGRA8Unorm:MTLPixelFormatRGBA8Unorm;
            MTLTextureDescriptor *t=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:width height:height mipmapped:NO];t.usage=MTLTextureUsageRenderTarget;
            id<MTLTexture> target=[device newTextureWithDescriptor:t];
            id<MTLFunction> fs=[library newFunctionWithName:@"lemonade_fetch_half"];
            MTLRenderPipelineDescriptor *p=[[MTLRenderPipelineDescriptor alloc] init];p.vertexFunction=vertex;p.fragmentFunction=fs;p.colorAttachments[0].pixelFormat=format;
            id<MTLRenderPipelineState> largePipeline=[device newRenderPipelineStateWithDescriptor:p error:&error];
            const float initial[4]={.2,.4,.6,.8},a[4]={.25,.125,.5,.5},b[4]={.05,.1,.2,.25};
            id<MTLBuffer> ab=[device newBufferWithBytes:a length:sizeof(a) options:MTLResourceStorageModeShared],bb=[device newBufferWithBytes:b length:sizeof(b) options:MTLResourceStorageModeShared];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(initial[0],initial[1],initial[2],initial[3]);
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
            [encoder setRenderPipelineState:largePipeline];[encoder setVertexBuffer:z offset:0 atIndex:0];[encoder setFragmentBuffer:ab offset:0 atIndex:0];[encoder setScissorRect:(MTLScissorRect){511,333,500,1655}];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
            [encoder setFragmentBuffer:bb offset:0 atIndex:0];[encoder setScissorRect:(MTLScissorRect){0,0,width,height}];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];BOOL done=finish(command);unsigned different=0;
            uint8_t *pixels=malloc(width*height*4);memset(pixels,0xa5,width*height*4);
            [target getBytes:pixels bytesPerRow:width*4 fromRegion:MTLRegionMake2D(0,0,width,height) mipmapLevel:0];
            for(unsigned y=0;y<height;y++)for(unsigned x=0;x<width;x++)for(unsigned c=0;c<4;c++) {
                BOOL inside=x>=511&&x<1011&&y>=333&&y<1988;
                float value=b[c]+(inside?a[c]+initial[c]*.5f:initial[c])*.75f;
                unsigned channel=formatIndex&&c<3?2-c:c;different+=abs((int)pixels[(y*width+x)*4+channel]-(int)lroundf(value*255))>3;
            }
            if(different)printf("lemonade-metal: fullsize-values format=%u corner=%u,%u,%u,%u center=%u,%u,%u,%u\n",formatIndex,pixels[0],pixels[1],pixels[2],pixels[3],pixels[(1200*width+600)*4],pixels[(1200*width+600)*4+1],pixels[(1200*width+600)*4+2],pixels[(1200*width+600)*4+3]);
            result(formatIndex?"compositor-fullsize-bgra-multidraw-fetch":"compositor-fullsize-rgba-multidraw-fetch",done&&largePipeline&&!different,command.status,different);
            free(pixels);[target release];[fs release];[p release];[largePipeline release];[ab release];[bb release];
        }
        for(unsigned edgeMode=0;edgeMode<6;edgeMode++) @autoreleasepool {
            unsigned halfMath=edgeMode&1;
            MTLTextureDescriptor *t=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:256 height:256 mipmapped:NO];t.usage=MTLTextureUsageRenderTarget;
            id<MTLTexture> target=[device newTextureWithDescriptor:t];
            NSString *edgeNames[]={@"lemonade_rounded",@"lemonade_rounded_half",@"lemonade_derivative_float",@"lemonade_derivative_half",@"lemonade_rounded_derivative",@"lemonade_rounded_derivative_half"};
            id<MTLFunction> fs=[library newFunctionWithName:edgeNames[edgeMode]];
            MTLRenderPipelineDescriptor *p=[[MTLRenderPipelineDescriptor alloc] init];p.vertexFunction=vertex;p.fragmentFunction=fs;p.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
            id<MTLRenderPipelineState> edgePipeline=[device newRenderPipelineStateWithDescriptor:p error:&error];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];[encoder setRenderPipelineState:edgePipeline];[encoder setVertexBuffer:z offset:0 atIndex:0];[encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];BOOL done=finish(command);
            uint8_t *pixels=malloc(256*256*4);memset(pixels,0xa5,256*256*4);[target getBytes:pixels bytesPerRow:1024 fromRegion:MTLRegionMake2D(0,0,256,256) mipmapLevel:0];unsigned different=0,maxDelta=0;
            for(unsigned y=0;y<256;y++)for(unsigned x=0;x<256;x++) {
                float qx=fabsf(x+.5f-128)-80,qy=fabsf(y+.5f-128)-80;
                float distance=hypotf(fmaxf(qx,0),fmaxf(qy,0))+fminf(fmaxf(qx,qy),0)-24;
                unsigned expected=lroundf(fminf(fmaxf(.5f-distance,0),1)*255);
                if(edgeMode>=4&&fabsf(distance)<2)continue;
                for(unsigned c=0;c<4;c++){
                    unsigned want=edgeMode==2||edgeMode==3?(unsigned[]){64,64,96,255}[c]:expected;
                    unsigned delta=abs((int)pixels[(y*256+x)*4+c]-(int)want);if(delta>maxDelta)maxDelta=delta;
                    if(delta>(halfMath?5:2)){if(different<4)printf("lemonade-metal: derivative-pixel mode=%u xy=%u,%u channel=%u got=%u expected=%u\n",edgeMode,x,y,c,pixels[(y*256+x)*4+c],want);different++;}
                }
            }
            printf("lemonade-metal: edge-detail mode=%u half=%u maximum_error=%u\n",edgeMode,halfMath,maxDelta);
            const char *testNames[]={"compositor-rounded-float-math","compositor-rounded-half-math","derivative-linear-float","derivative-linear-half","rounded-derivative-float","rounded-derivative-half"};
            result(testNames[edgeMode],done&&edgePipeline&&!different,command.status,different);
            free(pixels);[target release];[fs release];[p release];[edgePipeline release];
        }
        {
            id<MTLFunction> vs=[library newFunctionWithName:@"lemonade_link_vertex"],fs=[library newFunctionWithName:@"lemonade_link_fragment"];
            MTLRenderPipelineDescriptor *p=[[MTLRenderPipelineDescriptor alloc] init];p.vertexFunction=vs;p.fragmentFunction=fs;p.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
            id<MTLRenderPipelineState> linkPipeline=[device newRenderPipelineStateWithDescriptor:p error:&error];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=color;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];[encoder setRenderPipelineState:linkPipeline];[encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];BOOL done=finish(command);uint8_t pixels[1024];unsigned different=0;
            [color getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
            for(unsigned y=0;y<16;y++)for(unsigned x=0;x<16;x++){unsigned e[]={64,(unsigned)lroundf((x+.5f)/16*255),(unsigned)lroundf((y+.5f)/16*255),159};for(unsigned c=0;c<4;c++)different+=abs((int)pixels[(y*16+x)*4+c]-(int)e[c])>1;}
            result("varying-link-by-semantic-reordered",done&&linkPipeline&&!different,command.status,different);
            [vs release];[fs release];[p release];[linkPipeline release];
        }
        /* Exercise actual shared IOSurface ownership: cached CPU pixels,
         * partial GPU rendering, a completion fence, then direct CPU reading.
         * This is distinct from a texture map/read that may hide cache errors. */
        for(unsigned large=0;large<2;large++) @autoreleasepool {
            unsigned w=large?1125:65,h=large?2436:33,iterations=large?4:32;
            NSDictionary *properties=@{(id)kIOSurfaceWidth:@(w),(id)kIOSurfaceHeight:@(h),
                (id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfacePixelFormat:@(0x42475241)};
            IOSurfaceRef surface=IOSurfaceCreate((CFDictionaryRef)properties);
            MTLTextureDescriptor *t=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];t.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;t.storageMode=MTLStorageModeShared;
            id<MTLTexture> target=surface?[device newTextureWithDescriptor:t iosurface:surface plane:0]:nil;
            MTLRenderPipelineDescriptor *p=[[MTLRenderPipelineDescriptor alloc] init];p.vertexFunction=vertex;p.fragmentFunction=fragment;p.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
            id<MTLRenderPipelineState> surfacePipeline=[device newRenderPipelineStateWithDescriptor:p error:&error];
            unsigned different=0,badStatus=0;NSUInteger lastStatus=0;
            if(!target||!surfacePipeline)different=1;
            for(unsigned iteration=0;target&&surfacePipeline&&iteration<iterations;iteration++) @autoreleasepool {
                uint32_t seed=0;kern_return_t locked=IOSurfaceLock(surface,0,&seed);
                size_t pitch=IOSurfaceGetBytesPerRow(surface);uint8_t *base=IOSurfaceGetBaseAddress(surface);
                if(locked||!base){different++;break;}
                for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++) {
                    uint8_t *pixel=base+y*pitch+x*4;pixel[0]=(x+iteration*13)&255;pixel[1]=(y+iteration*17)&255;pixel[2]=(x+y+iteration*23)&255;pixel[3]=255;
                }
                IOSurfaceUnlock(surface,0,&seed);
                float values[4]={(iteration&1)?1:0,(iteration&2)?1:0,.5,1};
                id<MTLBuffer> input=[device newBufferWithBytes:values length:sizeof(values) options:MTLResourceStorageModeShared];
                MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionLoad;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
                id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];[encoder setRenderPipelineState:surfacePipeline];[encoder setVertexBuffer:z offset:0 atIndex:0];[encoder setFragmentBuffer:input offset:0 atIndex:0];[encoder setScissorRect:(MTLScissorRect){3,5,w-6,h-10}];[encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];
                BOOL done=finish(command);lastStatus=command.status;badStatus+=!done;
                locked=IOSurfaceLock(surface,kIOSurfaceLockReadOnly,&seed);
                if(locked){different++;[input release];break;}
                for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++) {
                    BOOL gpu=x>=3&&x<w-3&&y>=5&&y<h-5;uint8_t *pixel=base+y*pitch+x*4;
                    unsigned expected[4]={gpu?128:(x+iteration*13)&255,gpu?(unsigned)(values[1]*255):(y+iteration*17)&255,gpu?(unsigned)(values[0]*255):(x+y+iteration*23)&255,255};
                    for(unsigned c=0;c<4;c++)different+=abs((int)pixel[c]-(int)expected[c])>(gpu&&c==0?1:0);
                }
                IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,&seed);[input release];
            }
            result(large?"iosurface-cpu-gpu-ownership-fullsize-4":"iosurface-cpu-gpu-ownership-padded-32",!badStatus&&!different,lastStatus,different);
            [target release];[surfacePipeline release];[p release];if(surface)CFRelease(surface);
        }
        /* Metal flat interpolation uses the first (provoking) vertex. Both
         * triangle lists and strips must agree, including indexed ordering. */
        for(unsigned strip=0;strip<2;strip++) for(unsigned indexed=0;indexed<2;indexed++) @autoreleasepool {
            const float listPositions[]={-1,-1,1,-1,-1,1,1,-1,1,1,-1,1};
            const float stripPositions[]={-1,-1,1,-1,-1,1,1,1};
            const uint16_t listIndices[]={2,0,1,5,3,4},stripIndices[]={0,1,2,3};
            id<MTLBuffer> positions=[device newBufferWithBytes:strip?stripPositions:listPositions length:strip?sizeof(stripPositions):sizeof(listPositions) options:MTLResourceStorageModeShared];
            id<MTLBuffer> indices=[device newBufferWithBytes:strip?stripIndices:listIndices length:strip?sizeof(stripIndices):sizeof(listIndices) options:MTLResourceStorageModeShared];
            id<MTLFunction> vs=[library newFunctionWithName:@"lemonade_flat_vertex"],fs=[library newFunctionWithName:@"lemonade_flat_fragment"];
            MTLRenderPipelineDescriptor *p=[[MTLRenderPipelineDescriptor alloc] init];p.vertexFunction=vs;p.fragmentFunction=fs;p.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
            id<MTLRenderPipelineState> flatPipeline=[device newRenderPipelineStateWithDescriptor:p error:&error];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=color;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];[encoder setRenderPipelineState:flatPipeline];[encoder setVertexBuffer:positions offset:0 atIndex:0];
            if(indexed)[encoder drawIndexedPrimitives:strip?MTLPrimitiveTypeTriangleStrip:MTLPrimitiveTypeTriangle indexCount:strip?4:6 indexType:MTLIndexTypeUInt16 indexBuffer:indices indexBufferOffset:0];
            else [encoder drawPrimitives:strip?MTLPrimitiveTypeTriangleStrip:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:strip?4:6];
            [encoder endEncoding];BOOL done=finish(command);uint8_t pixels[1024];unsigned different=0;
            [color getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
            for(unsigned y=0;y<16;y++)for(unsigned x=0;x<16;x++)if(x!=y){unsigned first=x<y?(strip||!indexed?0:2):(strip?1:(indexed?5:3));unsigned expected=(unsigned)lroundf((first+1)*.125f*255);different+=abs((int)pixels[(y*16+x)*4]-(int)expected)>1;different+=pixels[(y*16+x)*4+1]!=0||pixels[(y*16+x)*4+2]!=0||pixels[(y*16+x)*4+3]!=255;}
            printf("lemonade-metal: flat-values strip=%u indexed=%u first=%u second=%u\n",strip,indexed,pixels[(12*16+3)*4],pixels[(3*16+12)*4]);
            char name[80];snprintf(name,sizeof(name),"flat-provoking-first-%s-%s",strip?"strip":"triangles",indexed?"indexed":"direct");result(name,done&&flatPipeline&&!different,command.status,different);
            [positions release];[indices release];[vs release];[fs release];[p release];[flatPipeline release];
        }
        /* Unsized constant arrays: a static access may use a bounded upload,
         * while a runtime index must retain the full available buffer range.
         * Offset 16 is valid for float4 but not the Adreno UBO alignment (64). */
        for(unsigned dynamic=0;dynamic<2;dynamic++) @autoreleasepool {
            id<MTLBuffer> values=[device newBufferWithLength:4096 options:MTLResourceStorageModeShared];
            uint32_t index=127;const float expectedColor[4]={.25,.5,.75,1};
            id<MTLBuffer> selected=[device newBufferWithBytes:&index length:sizeof(index) options:MTLResourceStorageModeShared];
            if(!values.contents||!selected){result("uniform-unsized-allocation",NO,0,1);[values release];[selected release];continue;}
            memset(values.contents,0,4096);memcpy((char *)values.contents+16+(dynamic?127:3)*16,expectedColor,sizeof(expectedColor));
            float value=.5f;memcpy(z.contents,&value,sizeof(value));
            id<MTLFunction> fs=[library newFunctionWithName:dynamic?@"lemonade_uniform_dynamic":@"lemonade_uniform_static"];
            MTLRenderPipelineDescriptor *p=[[MTLRenderPipelineDescriptor alloc] init];p.vertexFunction=vertex;p.fragmentFunction=fs;p.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
            id<MTLRenderPipelineState> uniformPipeline=[device newRenderPipelineStateWithDescriptor:p error:&error];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=color;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> command=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];[encoder setRenderPipelineState:uniformPipeline];[encoder setVertexBuffer:z offset:0 atIndex:0];[encoder setFragmentBuffer:values offset:16 atIndex:0];if(dynamic)[encoder setFragmentBuffer:selected offset:0 atIndex:1];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];[encoder endEncoding];BOOL done=finish(command);
            uint8_t pixels[1024];[color getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];unsigned different=0;
            for(unsigned pixel=0;pixel<256;pixel++)for(unsigned c=0;c<4;c++)different+=abs((int)pixels[pixel*4+c]-(int)lroundf(expectedColor[c]*255))>1;
            result(dynamic?"uniform-unsized-dynamic-index-127":"uniform-unsized-static-index-3",done&&uniformPipeline&&!different,command.status,different);
            [values release];[selected release];[fs release];[p release];[uniformPipeline release];
        }
        lifetimeTests(device,queue,library);
        printf("lemonade-metal: feature summary checks=%u failed=%u\n", checks, failures);
        [src release]; [dst release]; [compute release]; [kernel release]; [output release]; [countBuffer release];
        [pipeline release]; [pd release]; [vertex release]; [fragment release]; [library release];
        [queue release]; [color release]; [depth release]; [z release]; [rgba release];
        return failures ? 1 : 0;
    }
}

#ifdef LEMONADE_NATIVE_MAIN
int main(int argc, char **argv) {
    @autoreleasepool { return argc == 2 ? lemonade_metal_features(MTLCreateSystemDefaultDevice(), argv[1]) : 2; }
}
#endif
