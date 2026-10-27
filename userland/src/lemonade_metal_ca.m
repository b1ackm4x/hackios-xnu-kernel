/* Public CoreAnimation/Metal integration, runnable on Apple hardware too. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/QuartzCore.h>
#import <CoreGraphics/CoreGraphics.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    @autoreleasepool {
        setenv("REDWOOD_METAL_TRACE","1",1);
        id<MTLDevice> device=MTLCreateSystemDefaultDevice();
        id<MTLCommandQueue> queue=[device newCommandQueue];
        unsigned failures=0,checks=0;
        const unsigned width=512,height=512;
        CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        const CGFloat blackComponents[]={0,0,0,1},whiteComponents[]={1,1,1,1};
        CGColorRef black=CGColorCreate(space,blackComponents),white=CGColorCreate(space,whiteComponents);
        printf("lemonade-metal: CA device=%s\n",device.name.UTF8String);
        for(unsigned variant=0;variant<4;variant++) @autoreleasepool {
            printf("lemonade-metal: CA begin variant=%u\n",variant);
            MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:width height:height mipmapped:NO];
            td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
            id<MTLTexture> target=[device newTextureWithDescriptor:td];
            CARenderer *renderer=target?[CARenderer rendererWithMTLTexture:target options:@{kCARendererMetalCommandQueue:queue,kCARendererColorSpace:(id)space}]:nil;
            CALayer *root=[CALayer layer];root.frame=CGRectMake(0,0,width,height);root.backgroundColor=black;
            CALayer *shape=variant==2?[CAShapeLayer layer]:[CALayer layer];
            shape.frame=CGRectMake(64,64,384,384);
            if(variant==2){CGPathRef path=CGPathCreateWithRoundedRect(CGRectMake(0,0,384,384),64,64,NULL);[(CAShapeLayer *)shape setPath:path];[(CAShapeLayer *)shape setFillColor:white];CGPathRelease(path);}
            else {shape.backgroundColor=white;shape.cornerRadius=64;shape.cornerCurve=variant==1?kCACornerCurveContinuous:kCACornerCurveCircular;shape.masksToBounds=YES;}
            if(variant==3){shape.backgroundColor=black;CALayer *child=[CALayer layer];child.frame=shape.bounds;child.backgroundColor=white;[shape addSublayer:child];}
            [root addSublayer:shape];renderer.layer=root;renderer.bounds=root.bounds;
            [CATransaction flush];
            uint8_t *pixels=malloc(width*height*4);
            unsigned different=0,nonmonochrome=0,frames=0;
            NSUInteger status=0;
            for(unsigned frame=0;renderer&&frame<3;frame++) @autoreleasepool {
                [renderer beginFrameAtTime:CACurrentMediaTime() timeStamp:NULL];
                [renderer addUpdateRect:root.bounds];[renderer render];[renderer endFrame];
                id<MTLCommandBuffer> fence=[queue commandBuffer];[fence commit];[fence waitUntilCompleted];status=fence.status;
                if(status!=MTLCommandBufferStatusCompleted){different++;break;}
                memset(pixels,0xa5,width*height*4);
                [target getBytes:pixels bytesPerRow:width*4 fromRegion:MTLRegionMake2D(0,0,width,height) mipmapLevel:0];
                for(unsigned y=0;y<height;y++)for(unsigned x=0;x<width;x++) {
                    float qx=fabsf(x+.5f-256)-128,qy=fabsf(y+.5f-256)-128;
                    float sd=hypotf(fmaxf(qx,0),fmaxf(qy,0))+fminf(fmaxf(qx,qy),0)-64;
                    /* Continuous corners differ from circles. Only their straight
                     * interior/exterior regions share the same analytic oracle. */
                    BOOL known=variant!=1||qx<0||qy<0;
                    if(known&&fabsf(sd)>2){unsigned expected=sd<0?255:0;uint8_t *p=pixels+(y*width+x)*4;
                        unsigned delta=abs((int)p[0]-(int)expected);
                        if(delta>2){if(different<8)printf("lemonade-metal: CA pixel variant=%u frame=%u xy=%u,%u value=%u expected=%u distance=%.2f\n",variant,frame,x,y,p[0],expected,sd);different++;}
                        nonmonochrome+=p[0]!=p[1]||p[0]!=p[2];
                    }
                }
                frames++;
            }
            BOOL ok=renderer&&frames==3&&!different&&!nonmonochrome;
            printf("lemonade-metal: CA %s %s frames=%u status=%lu mismatches=%u color_mismatches=%u\n",(const char *[]) {"circular-corner","continuous-corner","rounded-shape-path","clipped-child"}[variant],ok?"PASS":"FAIL",frames,(unsigned long)status,different,nonmonochrome);
            checks++;failures+=!ok;free(pixels);renderer.layer=nil;[target release];
        }
        printf("lemonade-metal: CA summary checks=%u failed=%u\n",checks,failures);
        CGColorRelease(black);CGColorRelease(white);CGColorSpaceRelease(space);[queue release];
        return failures?1:0;
    }
}
