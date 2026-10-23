#include <metal_stdlib>
using namespace metal;

/* Explicit test inputs, compiled for iOS 16.5. These shaders are ours; tests do
 * not need to infer semantics from a private system shader's name. */
vertex float4 lemonade_vertex(uint id [[vertex_id]], constant float &depth [[buffer(0)]])
{
    const float2 positions[] = { float2(-1,-1), float2(1,-1), float2(-1,1), float2(1,1) };
    return float4(positions[id], depth, 1.0);
}

struct IntegerTargets {
    uint4 a [[color(0)]];
    uint4 b [[color(1)]];
    uint4 c [[color(2)]];
};
fragment IntegerTargets lemonade_integer(constant uint4 &color [[buffer(0)]])
{
    return { color, color, color };
}

fragment float4 lemonade_color(constant float4 &color [[buffer(0)]])
{
    return color;
}

kernel void lemonade_compute(device uint *output [[buffer(0)]],
                             constant uint &count [[buffer(1)]],
                             uint index [[thread_position_in_grid]])
{
    if (index < count) output[index] = index * 7 + 3;
}

kernel void lemonade_image_float(texture2d<float, access::read> source [[texture(0)]],
                                  texture2d<float, access::write> target [[texture(1)]],
                                  uint2 xy [[thread_position_in_grid]])
{
    if (xy.x < target.get_width() && xy.y < target.get_height()) target.write(source.read(xy), xy);
}
kernel void lemonade_image_half(texture2d<half, access::read> source [[texture(0)]],
                                 texture2d<half, access::write> target [[texture(1)]],
                                 uint2 xy [[thread_position_in_grid]])
{
    if (xy.x < target.get_width() && xy.y < target.get_height()) target.write(source.read(xy), xy);
}

kernel void lemonade_image_params(texture2d<float, access::read> source [[texture(1)]],
                                   texture2d<float, access::write> target [[texture(0)]],
                                   constant ushort4 &shape [[buffer(0)]],
                                   ushort2 xy [[thread_position_in_grid]])
{
    if (xy.x < shape.x && xy.y < shape.y) target.write(source.read(uint2(xy)), uint2(xy));
}
kernel void lemonade_image_sample(texture2d<float, access::sample> source [[texture(1)]],
                                   texture2d<float, access::write> target [[texture(0)]],
                                   sampler pixelSampler [[sampler(0)]],
                                   ushort2 xy [[thread_position_in_grid]])
{
    if (xy.x < target.get_width() && xy.y < target.get_height()) target.write(source.sample(pixelSampler,float2(xy)+0.5f),uint2(xy));
}

kernel void lemonade_image_constant(texture2d<float, access::write> target [[texture(0)]], uint2 xy [[thread_position_in_grid]])
{
    if(xy.x<17 && xy.y<17) target.write(float4(0.25,0.5,0.75,1),xy);
}
kernel void lemonade_image_fixed(texture2d<float, access::read> source [[texture(1)]],texture2d<float, access::write> target [[texture(0)]],uint2 xy [[thread_position_in_grid]])
{
    if(xy.x<17 && xy.y<17) target.write(source.read(xy),xy);
}

fragment float4 lemonade_fetch(float4 previous [[color(0)]],constant float4 &color [[buffer(0)]])
{
    return color+previous*(1.0f-color.a);
}
fragment half4 lemonade_fetch_half(half4 previous [[color(0)]],constant float4 &color [[buffer(0)]])
{
    return half4(color)+previous*(1.0h-half(color.a));
}
struct TwoTargets { float4 a [[color(0)]]; float4 b [[color(1)]]; };
fragment TwoTargets lemonade_two(constant float4 &color [[buffer(0)]])
{
    return {color,float4(.1,.2,.3,.4)};
}
fragment TwoTargets lemonade_fetch_two(float4 a [[color(0)]],float4 b [[color(1)]],constant float4 &color [[buffer(0)]])
{
    return {color+a*(1.0f-color.a),b*.5f};
}

struct LemonadeInput { float2 position [[attribute(0)]]; float2 uv [[attribute(1)]]; half4 tint [[attribute(2)]]; };
struct LemonadeSampleVaryings { float4 position [[position]]; float2 uv; half4 tint; };
vertex LemonadeSampleVaryings lemonade_sample_vertex(LemonadeInput in [[stage_in]])
{
    return {float4(in.position,0.5,1),in.uv,in.tint};
}
fragment float4 lemonade_sample_float(LemonadeSampleVaryings in [[stage_in]], texture2d<float> source [[texture(0)]],sampler s [[sampler(0)]])
{
    return source.sample(s,in.uv)*float4(in.tint);
}
fragment half4 lemonade_sample_half(LemonadeSampleVaryings in [[stage_in]], texture2d<half> source [[texture(0)]],sampler s [[sampler(0)]])
{
    return source.sample(s,in.uv)*in.tint;
}

kernel void lemonade_stencil_read(texture2d<uint,access::read> source [[texture(0)]],device uint *output [[buffer(0)]],uint2 xy [[thread_position_in_grid]])
{
    if(xy.x<16&&xy.y<16)output[xy.y*16+xy.x]=source.read(xy).r;
}

fragment half4 lemonade_rounded(float4 position [[position]])
{
    float2 q=abs(position.xy-float2(128))-float2(80);
    float distance=length(max(q,0.0f))+min(max(q.x,q.y),0.0f)-24.0f;
    float alpha=saturate(0.5f-distance);
    return half4(half(alpha));
}
fragment half4 lemonade_rounded_half(float4 position [[position]])
{
    half2 q=abs(half2(position.xy)-half2(128))-half2(80);
    half distance=length(max(q,0.0h))+min(max(q.x,q.y),0.0h)-24.0h;
    half alpha=saturate(0.5h-distance);
    return half4(alpha);
}

fragment float4 lemonade_derivative_float(float4 p [[position]])
{
    float v=p.x*.03125f+p.y*.0625f;
    return float4(dfdx(v)*8,dfdy(v)*4,fwidth(v)*4,1);
}
fragment half4 lemonade_derivative_half(float4 p [[position]])
{
    half v=half(p.x)*.03125h+half(p.y)*.0625h;
    return half4(dfdx(v)*8,dfdy(v)*4,fwidth(v)*4,1);
}
fragment half4 lemonade_rounded_derivative(float4 position [[position]])
{
    float2 q=abs(position.xy-float2(128))-float2(80);
    float distance=length(max(q,0.0f))+min(max(q.x,q.y),0.0f)-24.0f;
    return half4(half(saturate(.5f-distance/max(fwidth(distance),.001f))));
}
fragment half4 lemonade_rounded_derivative_half(float4 position [[position]])
{
    half2 q=abs(half2(position.xy)-half2(128))-half2(80);
    half distance=length(max(q,0.0h))+min(max(q.x,q.y),0.0h)-24.0h;
    return half4(saturate(.5h-distance/max(fwidth(distance),.001h)));
}

struct LemonadeLinkOut { float4 p [[position]]; float2 uv [[user(uv)]]; half4 tint [[user(tint)]]; };
struct LemonadeLinkIn { float4 p [[position]]; half4 tint [[user(tint)]]; float2 uv [[user(uv)]]; };
vertex LemonadeLinkOut lemonade_link_vertex(uint id [[vertex_id]])
{
    float2 p[]={float2(-1,-1),float2(1,-1),float2(-1,1),float2(1,1)};
    return {float4(p[id],.5,1),float2((p[id].x+1)*.5,(1-p[id].y)*.5),half4(.25,.5,.75,.625)};
}
fragment float4 lemonade_link_fragment(LemonadeLinkIn in [[stage_in]])
{
    return float4(in.tint.r,in.uv.x,in.uv.y,in.tint.a);
}

struct LemonadeFlat { float4 position [[position]]; float value [[flat]]; };
vertex LemonadeFlat lemonade_flat_vertex(uint id [[vertex_id]],constant float2 *positions [[buffer(0)]])
{
    return {float4(positions[id],.5,1),float(id+1)*.125f};
}
fragment float4 lemonade_flat_fragment(LemonadeFlat in [[stage_in]])
{
    return float4(in.value,0,0,1);
}

fragment float4 lemonade_uniform_static(constant float4 *values [[buffer(0)]])
{
    return values[3];
}
fragment float4 lemonade_uniform_dynamic(constant float4 *values [[buffer(0)]],
                                         constant uint &index [[buffer(1)]])
{
    return values[index];
}
