/* Bounded HID keyboard report decoding shared by the native bridge and tests.
 * Descriptors and reports come from the real peripheral. No key data is logged.
 * This is a keyboard/consumer-control adapter, not a general HID implementation.
 */
#ifndef LEMONADE_BT_HID_REPORT_H
#define LEMONADE_BT_HID_REPORT_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

#define LBH_REPORTS 16u
#define LBH_FIELDS 64u
#define LBH_USAGES 64u
#define LBH_KEYS 128u
#define LBH_BYTES 256u
struct lbh_keys { uint32_t value[LBH_KEYS]; unsigned count; };
struct lbh_report {
    uint8_t id;
    unsigned input_bits, output_bits, feature_bits;
    struct lbh_keys down;
    uint8_t output[LBH_BYTES];
};
struct lbh_field {
    uint8_t report, kind, size;
    unsigned offset, count, flags;
    int32_t logical_min;
    uint32_t logical_max, usage_min, usage_max, usages[LBH_USAGES];
    unsigned usage_count;
};
struct lbh_device {
    struct lbh_report reports[LBH_REPORTS]; unsigned report_count;
    struct lbh_field fields[LBH_FIELDS]; unsigned field_count;
    struct lbh_keys delivered;
    bool report_ids, keyboard;
};
struct lbh_global {
    uint32_t page, size, count, id, logical_max;
    int32_t logical_min;
};
struct lbh_local {
    uint32_t usages[LBH_USAGES], minimum, maximum;
    unsigned count;
    bool has_min, has_max;
};
static inline bool lbh_has(const struct lbh_keys *keys, uint32_t value) {
    for (unsigned i=0;i<keys->count;i++) if (keys->value[i]==value) return true;
    return false;
}
static inline bool lbh_add(struct lbh_keys *keys, uint32_t value) {
    if (lbh_has(keys,value)) return true;
    if (keys->count==LBH_KEYS) return false;
    keys->value[keys->count++]=value; return true;
}
static inline uint32_t lbh_usage(const struct lbh_field *f, unsigned index) {
    if (f->usage_count) return f->usages[index<f->usage_count?index:f->usage_count-1];
    uint32_t span=f->usage_max-f->usage_min;
    return f->usage_min+(index<span?index:span);
}
static inline bool lbh_key_usage(uint32_t usage) {
    unsigned page=usage>>16, value=usage&0xffff;
    return value && (page==7 || page==12 || (page==1 && value>=0x81 && value<=0x83));
}
static inline bool lbh_modifier(uint32_t usage) {
    return usage>=0x700e0 && usage<=0x700e7;
}
static inline int lbh_report_index(struct lbh_device *d, unsigned id, bool create) {
    for (unsigned i=0;i<d->report_count;i++) if (d->reports[i].id==id) return (int)i;
    if (!create || d->report_count==LBH_REPORTS) return -1;
    unsigned index=d->report_count++; d->reports[index].id=(uint8_t)id; return (int)index;
}
static inline bool lbh_descriptor(struct lbh_device *d, const uint8_t *data, size_t length) {
    if (!d || !data || !length || length>4096) return false;
    memset(d,0,sizeof(*d));
    struct lbh_global g={0}, stack[8]; unsigned pushed=0, depth=0;
    struct lbh_local local={0};
    for (size_t at=0;at<length;) {
        uint8_t prefix=data[at++];
        if (prefix==0xfe) return false; /* long items are not part of this adapter */
        unsigned size=prefix&3; if (size==3) size=4;
        if (size>length-at) return false;
        uint32_t value=0; for (unsigned i=0;i<size;i++) value|=(uint32_t)data[at++]<<(8*i);
        unsigned type=(prefix>>2)&3, tag=prefix>>4;
        if (type==1) {
            switch (tag) {
            case 0: if (value>0xffff) return false; g.page=value; break;
            case 1:
                g.logical_min=(int32_t)value;
                if (size && size<4 && (value&(1u<<(8*size-1)))) g.logical_min=(int32_t)(value|(~0u<<(8*size)));
                break;
            case 2:g.logical_max=value;break;
            case 7:if (value>32) return false;g.size=value;break;
            case 8:if (!value || value>255) return false;g.id=value;d->report_ids=true;break;
            case 9:if (value>256) return false;g.count=value;break;
            case 10:if (pushed==8 || size) return false;stack[pushed++]=g;break;
            case 11:if (!pushed || size) return false;g=stack[--pushed];break;
            default:break; /* physical bounds and units do not affect key states */
            }
        } else if (type==2) {
            uint32_t usage=size==4?value:(g.page<<16)|value;
            if (tag==0) {if (local.count==LBH_USAGES) return false;local.usages[local.count++]=usage;}
            else if (tag==1) {local.minimum=usage;local.has_min=true;}
            else if (tag==2) {local.maximum=usage;local.has_max=true;}
            else return false; /* designators, strings and delimiters unsupported */
        } else if (type==0) {
            if (tag==10) {
                if (depth==16 || size!=1) return false;
                uint32_t usage=local.count?local.usages[0]:local.minimum;
                if (value==1 && usage==0x10006) d->keyboard=true;
                depth++;
            } else if (tag==12) {
                if (!depth || size) return false;depth--;
            } else if (tag==8 || tag==9 || tag==11) {
                if (!depth || !size || !g.size || !g.count) return false;
                int ri=lbh_report_index(d,g.id,true); if (ri<0) return false;
                struct lbh_report *r=&d->reports[ri];
                unsigned *bits=tag==8?&r->input_bits:tag==9?&r->output_bits:&r->feature_bits;
                unsigned width=g.size*g.count;
                if (*bits>LBH_BYTES*8 || width>LBH_BYTES*8-*bits) return false;
                if (local.has_min!=local.has_max || (local.has_min &&
                    (local.minimum>local.maximum || (local.minimum>>16)!=(local.maximum>>16)))) return false;
                bool relevant=false;
                if (!(value&1)) {
                    if (local.count) {
                        for (unsigned i=0;i<local.count;i++)
                            relevant|=(tag==8&&lbh_key_usage(local.usages[i])) || (tag==9&&(local.usages[i]>>16)==8);
                    } else if (local.has_min) {
                        unsigned page=local.minimum>>16;
                        relevant=(tag==8&&(page==7||page==12||(page==1&&local.maximum>=0x10081&&local.minimum<=0x10083))) ||
                            (tag==9&&page==8);
                    }
                }
                if (relevant) {
                    if (d->field_count==LBH_FIELDS || g.logical_min<0 || g.logical_max>65535 || (value&4)) return false;
                    struct lbh_field *f=&d->fields[d->field_count++];
                    f->report=(uint8_t)ri;f->kind=(uint8_t)tag;f->size=(uint8_t)g.size;
                    f->offset=*bits;f->count=g.count;f->flags=value;f->logical_min=g.logical_min;f->logical_max=g.logical_max;
                    f->usage_min=local.minimum;f->usage_max=local.maximum;f->usage_count=local.count;
                    memcpy(f->usages,local.usages,local.count*sizeof(uint32_t));
                }
                *bits+=width;
            } else return false;
            memset(&local,0,sizeof(local));
        } else return false;
    }
    if (depth || pushed || !d->keyboard) return false;
    bool keys=false;
    for (unsigned i=0;i<d->field_count;i++) keys|=d->fields[i].kind==8;
    for (unsigned i=0;i<d->report_count;i++)
        if (d->report_ids && !d->reports[i].id) return false;
    return keys;
}
static inline uint32_t lbh_bits(const uint8_t *bytes, unsigned offset, unsigned width) {
    uint32_t result=0;
    for (unsigned bit=0;bit<width;bit++) result|=(uint32_t)((bytes[(offset+bit)/8]>>((offset+bit)%8))&1)<<bit;
    return result;
}
typedef bool (*lbh_emit_fn)(void *context, uint32_t usage, bool down);
static inline bool lbh_sync(struct lbh_device *d, lbh_emit_fn emit, void *context) {
    struct lbh_keys wanted={0};
    for (unsigned i=0;i<d->report_count;i++)
        for (unsigned k=0;k<d->reports[i].down.count;k++)
            if (!lbh_add(&wanted,d->reports[i].down.value[k])) return false;
    /* Release ordinary keys before modifiers; press modifiers before keys. */
    for (unsigned modifiers=0;modifiers<2;modifiers++)
        for (unsigned i=0;i<d->delivered.count;) {
            uint32_t key=d->delivered.value[i];
            if (lbh_modifier(key)==(bool)modifiers && !lbh_has(&wanted,key)) {
                if (!emit(context,key,false)) return false;
                d->delivered.value[i]=d->delivered.value[--d->delivered.count];
            } else i++;
        }
    for (unsigned pass=0;pass<2;pass++)
        for (unsigned i=0;i<wanted.count;i++) {
            uint32_t key=wanted.value[i];
            if (lbh_modifier(key)==(pass==0) && !lbh_has(&d->delivered,key)) {
                if (!emit(context,key,true)) return false;
                if (!lbh_add(&d->delivered,key)) return false;
            }
        }
    return true;
}
static inline bool lbh_input(struct lbh_device *d, const uint8_t *data, size_t length,
                             lbh_emit_fn emit, void *context) {
    if (!d || !data || !length || !emit) return false;
    unsigned id=d->report_ids?*data++:0; if (d->report_ids) length--;
    int ri=lbh_report_index(d,id,false); if (ri<0) return false;
    unsigned bits=d->reports[ri].input_bits;
    if (!bits || length!=(bits+7)/8) return false;
    struct lbh_keys keys={0};
    for (unsigned i=0;i<d->field_count;i++) {
        const struct lbh_field *f=&d->fields[i];
        if (f->report!=(unsigned)ri || f->kind!=8) continue;
        for (unsigned n=0;n<f->count;n++) {
            uint32_t value=lbh_bits(data,f->offset+n*f->size,f->size), usage;
            if (!value) continue;
            if (f->flags&2) usage=lbh_usage(f,n);
            else {
                if (value<(uint32_t)f->logical_min || value>f->logical_max) {
                    if (f->flags&0x40) continue;
                    return false;
                }
                unsigned index=value-(uint32_t)f->logical_min;
                if ((f->usage_count && index>=f->usage_count) ||
                    (!f->usage_count && index>f->usage_max-f->usage_min)) {
                    if (f->flags&0x40) continue;
                    return false;
                }
                usage=lbh_usage(f,index);
                if (usage>=0x70001 && usage<=0x70003) return true; /* preserve state during rollover */
            }
            if (lbh_key_usage(usage) && !lbh_add(&keys,usage)) return false;
        }
    }
    struct lbh_keys wanted=keys;
    for (unsigned i=0;i<d->report_count;i++) {
        if (i==(unsigned)ri) continue;
        for (unsigned k=0;k<d->reports[i].down.count;k++)
            if (!lbh_add(&wanted,d->reports[i].down.value[k])) return false;
    }
    d->reports[ri].down=keys;
    return lbh_sync(d,emit,context);
}
static inline bool lbh_release(struct lbh_device *d,lbh_emit_fn emit,void *context) {
    for (unsigned i=0;i<d->report_count;i++) d->reports[i].down.count=0;
    return lbh_sync(d,emit,context);
}
/* Update a standard keyboard LED report. Output callbacks send these real bytes
 * through the native HID profile; no controller/host result is synthesized. */
static inline int lbh_led(struct lbh_device *d,unsigned led,bool enabled,uint8_t output[LBH_BYTES+1],size_t *length) {
    if (!led || led>5 || !length) return -1;
    for (unsigned i=0;i<d->field_count;i++) {
        const struct lbh_field *f=&d->fields[i];
        if (f->kind!=9 || !(f->flags&2) || f->size!=1) continue;
        for (unsigned n=0;n<f->count;n++) {
            if (lbh_usage(f,n)!=(0x80000u|led)) continue;
            struct lbh_report *r=&d->reports[f->report];
            unsigned bit=f->offset+n;
            r->output[bit/8]=(uint8_t)((r->output[bit/8]&~(1u<<(bit%8)))|((unsigned)enabled<<(bit%8)));
            *length=(r->output_bits+7)/8;
            if (d->report_ids) {output[0]=r->id;memcpy(output+1,r->output,(*length)++);}
            else memcpy(output,r->output,*length);
            return r->id;
        }
    }
    return -1;
}
#endif
