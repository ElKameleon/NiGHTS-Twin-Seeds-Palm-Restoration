#!/usr/bin/env python3
"""NiGHTS into Dreams (Saturn) - Twin Seeds native palm restoration.

Self-contained, standard-library-only patcher for a verified original 21-track
single-BIN MODE1/2352 CUE. Creates NIGHTS_PATCHED.BIN/CUE alongside the input;
never alters or overwrites the source image. Includes custom SH-2 initialization,
PRS compression, disc ECC/EDC, texture resampling, and disc revision checks.

Usage:
    python Patch_NiGHTS_Palm.py "NIGHTS.CUE"
    python Patch_NiGHTS_Palm.py "NIGHTS.CUE" --dry-run
    python Patch_NiGHTS_Palm.py "NIGHTS.CUE" --check

The original game textures are read from the user's own disc image.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import struct
import sys
from pathlib import Path

OUTPUT_STEM = 'NIGHTS_PATCHED'

# ------------------------- Original image verification ---------------------
SECTOR_SIZE = 2352


SYNC = b'\x00' + (b'\xff'*10) + b'\x00'


DATA_START = 16


KNOWN = (
  dict(resource='PRG7P.PRS',sector=384,bytes=20089,sha256='d8e512eae0182b6755d3b7c197a7366c84d7b3e2e8468036fae66be171df8f8f'),
  dict(resource='RD7PH.PRS',sector=1269,bytes=53862,sha256='e2319241c6b787a05ef8588104d0ce84864b16ca0cc19282af9deaeb86c7cbfe'),
  dict(resource='RD7P.PRS',sector=1296,bytes=258582,sha256='b023cb7263c52ad7089ad9de0297dbf28a29025b44b146a4cdf394d12b2b876b'),
)


CUE_FILE = re.compile(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))\s+(\S+)\s*$',re.I)


TRACK = re.compile(r'^\s*TRACK\s+(\d+)\s+(\S+)\s*$',re.I)


INDEX = re.compile(r'^\s*INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)\s*$',re.I)


class UnsupportedImage(Exception):
    pass


def parse_cue(path:Path):
    # CUE files are typically UTF-8 or local Windows code page.
    try:
        cue = path.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError:
        cue = path.read_text(encoding='cp1252')
    files=[]; tracks=[]; indices=[]; current=None
    for row in cue.splitlines():
        s=row.strip()
        if not s or s.upper().startswith('REM '):
            continue
        m=CUE_FILE.fullmatch(row)
        if m:
            fn=m.group(1) or m.group(2)
            if Path(fn).is_absolute() or '/' in fn or '\\' in fn or fn in ('.','..'):
                raise UnsupportedImage('For safety, put CUE and referenced BIN files together in one directory, with simple filenames.')
            files.append((fn,m.group(3).upper()))
            current=fn
            continue
        m=TRACK.fullmatch(row)
        if m:
            tracks.append((int(m.group(1)),m.group(2).upper(),current))
            continue
        m=INDEX.fullmatch(row)
        if m and tracks:
            mm=int(m.group(3));ss=int(m.group(4))
            if mm>59 or ss>74:
                raise UnsupportedImage('Malformed CUE INDEX time')
            indices.append((tracks[-1][0],int(m.group(1)),int(m.group(2))*60*75+mm*75+ss))
    if not files:
        raise UnsupportedImage('No FILE entry in CUE')
    if not tracks or tracks[0][:2]!=(1,'MODE1/2352'):
        raise UnsupportedImage('Requires TRACK 01 MODE1/2352 in CUE')
    if tracks[0][2]!=files[0][0] or files[0][1]!='BINARY':
        raise UnsupportedImage('Track 01 must use the first FILE in BINARY format')
    index1=[pos for track,no,pos in indices if track==1 and no==1]
    if len(index1)!=1 or index1[0]!=0:
        raise UnsupportedImage('Requires TRACK 01 INDEX 01 00:00:00 with no preceding offset')
    # Single-BIN multi-track and separate audio files are fine for validation.
    data_name=files[0][0]
    matches=[x for x in path.parent.iterdir() if x.is_file() and x.name.casefold()==data_name.casefold()]
    if len(matches)!=1:
        raise UnsupportedImage(f'Could not resolve exactly one data BIN named {data_name!r} next to CUE')
    return matches[0],files,tracks


def read_iso_resource(f, entry):
    chunks=[]
    num_sectors=(entry['bytes']+2047)//2048
    for i in range(num_sectors):
        n=entry['sector']+i
        f.seek(n*SECTOR_SIZE)
        raw=f.read(SECTOR_SIZE)
        if len(raw)!=SECTOR_SIZE or raw[:12]!=SYNC or raw[15]!=1:
            raise UnsupportedImage(f'Invalid MODE1/2352 sector {n} while reading {entry["resource"]}')
        chunks.append(raw[DATA_START:DATA_START+2048])
    return b''.join(chunks)[:entry['bytes']]


def inspect(cue:Path):
    cue=cue.resolve()
    if not cue.is_file():
        raise UnsupportedImage('CUE path does not exist')
    data_bin, files, tracks=parse_cue(cue)
    size=data_bin.stat().st_size
    if size%SECTOR_SIZE:
        raise UnsupportedImage('Track 01 BIN is not a whole number of 2352-byte raw sectors')
    if size < (max(x['sector']+(x['bytes']+2047)//2048 for x in KNOWN))*SECTOR_SIZE:
        raise UnsupportedImage('Data BIN is too short for the known Twin Seeds resources')
    with data_bin.open('rb') as f:
        f.seek(16*SECTOR_SIZE)
        sector=f.read(SECTOR_SIZE)
        if sector[:12]!=SYNC or sector[15]!=1 or sector[16:23]!=b'\x01CD001\x01':
            raise UnsupportedImage('ISO9660 primary volume descriptor not present in MODE1 sector 16')
        matches=[]
        for entry in KNOWN:
            data=read_iso_resource(f,entry)
            actual=hashlib.sha256(data).hexdigest()
            matches.append({'resource':entry['resource'],'sector':entry['sector'],
                'expected_sha256':entry['sha256'],'actual_sha256':actual,'matched':actual==entry['sha256']})
    return {'cue':str(cue),'data_bin':str(data_bin),'data_bin_size':size,
        'cue_file_count':len(files),'track_count':len(tracks),'resources':matches,
        'supported_original':all(x['matched'] for x in matches),
        'release_status':'Native billboard restoration',
        'changes_made':False}

# ---------------------- Sega PRS decoder and encoder -----------------------
MAX_OUTPUT = 32 * 1024 * 1024

def decompress(data:bytes):
    cursor=0
    ctl=0
    mask=0
    output=bytearray()
    def take_byte():
        nonlocal cursor
        if cursor>=len(data):raise ValueError('Unexpected end of PRS stream')
        res=data[cursor];cursor+=1;return res
    def bit():
        nonlocal ctl,mask
        if mask==0:
            ctl=take_byte();mask=1
        result=bool(ctl&mask)
        mask<<=1
        if mask==256:mask=0
        return result
    while True:
        if bit():
            output.append(take_byte())
        elif bit():
            lo,hi=take_byte(),take_byte()
            if lo==0 and hi==0:
                if cursor<len(data):
                    remaining=data[cursor:]
                    if any(remaining):raise ValueError(f'Non-zero data after PRS terminator ({len(remaining)} bytes)')
                return bytes(output)
            offset=(((hi<<8)|lo)>>3)-8192
            length=(lo&7)
            length=(take_byte()+1) if length==0 else length+2
            if offset>=0 or -offset>len(output):raise ValueError('Bad long backreference')
            if len(output)+length>MAX_OUTPUT:raise ValueError('Output too large')
            for _ in range(length):output.append(output[offset])
        else:
            length=2+2*int(bit())+int(bit())
            offset=take_byte()-256
            if offset>=0 or -offset>len(output):raise ValueError('Bad short backreference')
            if len(output)+length>MAX_OUTPUT:raise ValueError('Output too large')
            for _ in range(length):output.append(output[offset])
        if len(output)>MAX_OUTPUT:raise ValueError('Output too large')

def _find_last(data, needle, i, window):
    # end excludes any match that starts at position >=i, but permits overlap
    return data.rfind(needle, max(0,i-window), i+len(needle)-1)


def _match(data,i,window,min_len,max_len):
    if max_len<min_len: return 0,0
    if _find_last(data, data[i:i+min_len], i, window)<0:
        return 0,0
    lo=min_len; hi=max_len
    last=_find_last(data,data[i:i+lo],i,window)
    while lo<hi:
        mid=(lo+hi+1)//2
        found=_find_last(data,data[i:i+mid],i,window)
        if found>=0:
            lo=mid;last=found
        else:
            hi=mid-1
    if last<0 or i-last>window:raise AssertionError('bad LZ match')
    return lo,i-last


def compress(data:bytes)->bytes:
    n=len(data)
    long=[(0,0)]*n;short=[(0,0)]*n
    for i in range(n):
        bound=min(256,n-i)
        long[i]=_match(data,i,8191,3,bound)
        short[i]=_match(data,i,256,2,min(bound,5))
    dp=[0]*(n+1);tok=[None]*n
    dp[n]=18
    for i in range(n-1,-1,-1):
        cost=9+dp[i+1];best=('lit',1,0)
        sl,sd=short[i]
        for ln in range(2,sl+1):
            new=12+dp[i+ln]
            if new<cost:cost=new;best=('short',ln,sd)
        ll,ld=long[i]
        for ln in range(3,min(ll,9)+1):
            new=18+dp[i+ln]
            if new<cost:cost=new;best=('long',ln,ld)
        for ln in range(10,ll+1):
            new=26+dp[i+ln]
            if new<cost:cost=new;best=('long',ln,ld)
        dp[i]=cost;tok[i]=best
    out=bytearray(); bit_index=8;ctl_index=-1
    def bit(b):
        nonlocal bit_index,ctl_index
        if bit_index==8:
            ctl_index=len(out);out.append(0);bit_index=0
        if b:out[ctl_index]|=1<<bit_index
        bit_index+=1
    i=0
    while i<n:
        mode,length,dist=tok[i]
        if mode=='lit':bit(1);out.append(data[i])
        elif mode=='short':
            bit(0);bit(0);bit(((length-2)>>1)&1);bit((length-2)&1)
            out.append((-dist)&255)
        else:
            bit(0);bit(1)
            packed=((-dist)&8191)<<3
            if length<=9:packed|=length-2
            out.extend((packed&255,(packed>>8)&255))
            if length>9:out.append(length-1)
        i+=length
    bit(0);bit(1);out.extend((0,0))
    return bytes(out)

# --------------------- Saturn Mode1/2352 EDC and ECC -----------------------
E_FWD = [0]*256


E_BACK = [0]*256


EDC_TAB = [0]*256
for i in range(256):
    j = ((i << 1) ^ (0x11D if (i & 0x80) else 0)) & 0xFF
    E_FWD[i] = j
    E_BACK[i ^ j] = i
    edc = i
    for _ in range(8):
        edc = (edc >> 1) ^ (0xD8018001 if (edc & 1) else 0)
    EDC_TAB[i] = edc

def cd_edc(data):
    acc = 0
    for b in data:
        acc = ((acc >> 8) ^ EDC_TAB[(acc ^ b) & 255]) & 0xFFFFFFFF
    return struct.pack('<I', acc)


def ecc_block(source, count_major, count_minor, mult, stride):
    size = count_major * count_minor
    if len(source) < size:
        raise ValueError('ECC source too short')
    dest = bytearray(count_major * 2)
    for major in range(count_major):
        index = (major >> 1) * mult + (major & 1)
        a = b = 0
        for _ in range(count_minor):
            v = source[index]
            index += stride
            if index >= size:
                index -= size
            a ^= v
            b ^= v
            a = E_FWD[a]
        a = E_BACK[E_FWD[a] ^ b]
        dest[major] = a
        dest[major + count_major] = a ^ b
    return dest


def fix_mode1(sector):
    """Update EDC (0x810), reserved bytes and ECC P/Q, keep other bytes."""
    sector[0x810:0x814] = cd_edc(sector[:0x810])
    sector[0x814:0x81C] = b'\x00' * 8
    sector[0x81C:0x8C8] = ecc_block(sector[0x0C:], 86, 24, 2, 86)
    sector[0x8C8:0x930] = ecc_block(sector[0x0C:], 52, 43, 86, 88)
    return sector

# --------------------- User-owned image texture scaling -------------------
TRUNK_W, TRUNK_H = 24, 198


CANOPY_W, CANOPY_H = 144, 88


TRUNK_SOURCE_RD = 0x40C80


CANOPY_SOURCE_RD = 0x406D0


TRUNK_SOURCE_DIMS = (24, 248)


CANOPY_SOURCE_DIMS = (64, 44)


TRUNK_SIZE = TRUNK_W * TRUNK_H // 2


CANOPY_SIZE = CANOPY_W * CANOPY_H // 2


BASE_LOW = 0x00200010


RD7P_ORIG_SIZE = 449804


TRUNK_EXTRA_RD = RD7P_ORIG_SIZE


CANOPY_EXTRA_RD = TRUNK_EXTRA_RD + TRUNK_SIZE


TRUNK_PTR = BASE_LOW + TRUNK_EXTRA_RD


CANOPY_PTR = BASE_LOW + CANOPY_EXTRA_RD


TRUNK_REG = 0x4804


CANOPY_REG = 0x4858


TRUNK_PAL_PTR = 0x00241890


CANOPY_PAL_PTR = 0x00240C70


def resize_indexed_4bpp(blob: bytes, w: int, h: int, nw: int, nh: int) -> bytes:
    if w % 2 or nw % 8 or len(blob) != w*h//2:
        raise ValueError('invalid pixel data for Saturn packed 4bpp texture')
    pixels=[]
    for byte in blob:
        pixels.extend((byte>>4,byte&15))
    out=bytearray(nw*nh//2)
    for y in range(nh):
        sy=y*h//nh
        for x in range(0,nw,2):
            sx=x*w//nw
            sx2=(x+1)*w//nw
            out[(y*nw+x)//2]=(pixels[sy*w+sx]<<4)|pixels[sy*w+sx2]
    return bytes(out)


def get_scaled_texture_bytes(rd7p_patched: bytes):
    if len(rd7p_patched)!=RD7P_ORIG_SIZE:
        raise ValueError(f'Unexpected RD7P plain size: {len(rd7p_patched)}')
    oldtrunk=rd7p_patched[TRUNK_SOURCE_RD:TRUNK_SOURCE_RD+TRUNK_SOURCE_DIMS[0]*TRUNK_SOURCE_DIMS[1]//2]
    oldcanopy=rd7p_patched[CANOPY_SOURCE_RD:CANOPY_SOURCE_RD+CANOPY_SOURCE_DIMS[0]*CANOPY_SOURCE_DIMS[1]//2]
    trunk=resize_indexed_4bpp(oldtrunk,*TRUNK_SOURCE_DIMS,TRUNK_W,TRUNK_H)
    canopy=resize_indexed_4bpp(oldcanopy,*CANOPY_SOURCE_DIMS,CANOPY_W,CANOPY_H)
    assert len(trunk)==TRUNK_SIZE and len(canopy)==CANOPY_SIZE
    return trunk,canopy


def append_scaled_art(rd7p_patched: bytes):
    trunk,canopy=get_scaled_texture_bytes(rd7p_patched)
    combined=rd7p_patched+trunk+canopy
    # Do not cross into following original Low Work RAM allocation at +0x70000.
    if len(combined)+16>0x70000:
        raise ValueError('Resized RD7P resources cross the stage RAM boundary')
    return combined


def patch_registration_table(prg_patched: bytes):
    p=bytearray(prg_patched)
    # Validate the known working legacy registrations first.
    assert p[TRUNK_REG:TRUNK_REG+12]==struct.pack('>IIHH',0x00240C90,TRUNK_PAL_PTR,0x03F8,1)
    assert p[CANOPY_REG:CANOPY_REG+12]==struct.pack('>IIHH',0x002406E0,CANOPY_PAL_PTR,0x082C,1)
    p[TRUNK_REG:TRUNK_REG+12]=struct.pack('>IIHH',TRUNK_PTR,TRUNK_PAL_PTR,0x03C6,1)
    p[CANOPY_REG:CANOPY_REG+12]=struct.pack('>IIHH',CANOPY_PTR,CANOPY_PAL_PTR,0x1258,1)
    return bytes(p)

# --------------------- Native SH-2 actor startup hook ----------------------
STAGE=0x060C0000


HOOK=0x060C9200


FLAG=0x060C9400


ACTOR_ADDRESS=0x060C9404


GROUP=0x060C9420


ACTOR_TEMPLATE=0x060C94A0


END=0x060C94F8


ORIGINAL_INIT=0x060C8838


ALLOC=0x0600D0D0


LIST=0x060FFFB8


TRUNK_SOURCE=0x060EDFE8


CANOPY_SOURCE=0x060EE020


TRUNK_DEST=0x060EF3A0


CANOPY_DEST=0x060EF3A8


DRAW_CALLBACK=0x060C8C4E


XYZ=(3784,4801,5626)


TEX_IDS=(0x0175,0x0176)


class SH2:
    def __init__(self,base):
        self.base=base; self.words=[]; self.labels={}; self.branches=[];self.lit_uses=[]
    def addr(self):return self.base+2*len(self.words)
    def emit(self,v):
        assert 0<=v<=0xffff
        self.words.append(v)
    def label(self,l):
        if l in self.labels:raise ValueError('duplicate label: '+l)
        self.labels[l]=self.addr()
    def lit(self,reg,key):
        i=len(self.words);self.emit(0xD000|(reg<<8));self.lit_uses.append((i,reg,key))
    def branch(self,op,label):
        i=len(self.words);self.emit({'bt':0x8900,'bf':0x8b00}[op]);self.branches.append((i,op,label))
    def code(self,constants):
        self.emit(0x0009) if self.addr()%4==2 else None # align literals
        end=self.addr();places={};pool=bytearray()
        for name,val in constants.items():
            places[name]=end+len(pool)
            pool+=struct.pack('>I',val)
        for i,op,l in self.branches:
            dest=self.labels[l];src=self.base+2*i
            d=(dest-(src+4))//2
            assert dest-(src+4)==d*2 and -128<=d<=127,(op,l,src,d)
            self.words[i]=(0x8900 if op=='bt' else 0x8b00)|(d&255)
        for i,reg,name in self.lit_uses:
            src=self.base+2*i
            start=(src+4)&~3
            diff=places[name]-start
            assert diff%4==0 and 0<=diff//4<=255,(name,hex(src),diff)
            self.words[i]=0xD000|(reg<<8)|(diff//4)
        return b''.join(struct.pack('>H',w) for w in self.words)+pool,places


def build_code():
    asm=SH2(HOOK)
    # Confirmed startup trampoline: save original caller PR, call original
    # stage initializer with R0 restored in the delay slot.
    asm.emit(0x4F22) # sts.l pr,@-r15
    asm.emit(0x2F06) # mov.l r0,@-r15
    asm.lit(0,'orig')
    asm.emit(0x400B) # jsr @r0
    asm.emit(0x60F6) # mov.l @r15+,r0 (delay slot)
    # From this point on, preserve all original post-initializer registers.
    for reg in range(15): asm.emit(0x2F06|(reg<<4))
    asm.emit(0x4F03) # stc.l sr,@-r15 (preserve T and interrupt flags)

    asm.lit(0,'flag')
    asm.emit(0x6202) # mov.l @r0,r2
    asm.emit(0x2228) # tst r2,r2
    asm.branch('bf','exit')
    asm.emit(0xE201) # mov #1,r2; mark attempted
    asm.emit(0x2022) # mov.l r2,@r0

    # Copy native scaled eight-byte descriptors into two previously blank
    # stage-local sprite ID slots used by our composite group.
    asm.lit(4,'trunksrc')
    asm.lit(5,'trunkdst')
    for _ in range(2):
        asm.emit(0x6646) # mov.l @r4+,r6
        asm.emit(0x2562) # mov.l r6,@r5
        asm.emit(0x7504) # add #4,r5
    asm.lit(4,'canopysrc')
    for _ in range(2):
        asm.emit(0x6646)
        asm.emit(0x2562)
        asm.emit(0x7504)

    # Invoke the game's native 96-byte object allocator with the same
    # active-list sentinel that the balloon spawner uses.
    asm.lit(2,'list')
    asm.lit(0,'allocator')
    asm.emit(0x400B)  # jsr @r0
    asm.emit(0x0009)  # nop delay slot
    asm.emit(0x2338)  # tst r3,r3
    asm.branch('bt','exit')
    asm.lit(0,'actor_out')
    asm.emit(0x2032)  # mov.l r3,@r0 (record allocated address)

    # Allocator owns +00/+04 links. Copy fixed sprite parameters at +08..+5F
    # exactly as in the successful RAM-only native palm actor.
    asm.emit(0x6433)  # mov r3,r4
    asm.emit(0x7408)  # add #8,r4
    asm.lit(5,'actor_tpl')
    asm.emit(0xE716)  # mov #22,r7 (88 bytes)
    asm.label('copy_loop')
    asm.emit(0x6656)  # mov.l @r5+,r6
    asm.emit(0x2462)  # mov.l r6,@r4
    asm.emit(0x7404)  # add #4,r4
    asm.emit(0x77FF)  # add #-1,r7
    asm.emit(0x4715)  # cmp/pl r7
    asm.branch('bt','copy_loop')
    asm.lit(0,'flag')
    asm.emit(0xE202)  # mov #2,r2: actor configured
    asm.emit(0x2022)  # mov.l r2,@r0

    asm.label('exit')
    asm.emit(0x4F07)  # ldc.l @r15+,sr
    for reg in reversed(range(15)):asm.emit(0x6006|(reg<<8)|(15<<4))
    asm.emit(0x4F26) # lds.l @r15+,pr
    asm.emit(0x000B) # rts
    asm.emit(0x0009) # nop delay slot

    keys={
        'orig':ORIGINAL_INIT,
        'flag':FLAG,
        'trunksrc':TRUNK_SOURCE,
        'trunkdst':TRUNK_DEST,
        'canopysrc':CANOPY_SOURCE,
        'list':LIST,
        'allocator':ALLOC,
        'actor_out':ACTOR_ADDRESS,
        'actor_tpl':ACTOR_TEMPLATE,
    }
    code,loc=asm.code(keys)
    assert HOOK+len(code)<=FLAG,(hex(HOOK+len(code)),hex(FLAG))
    return code,asm,loc


def sprite_template(tex_id,dx,dy,pmod):
    return struct.pack('>HhhHH5H',tex_id,dx,dy,pmod,1,0,0,0,0,0)


def group_bytes():
    result=bytearray(bytes.fromhex('0004 002e'))
    # Match the visually approved slot-5 sprite anchors exactly.
    # Old (pre-resize) offsets (-12,-248) and (-32,-294) make the
    # shortened trunk float and place the enlarged canopy off-center.
    for pmod in (0x1088,0x108C):
        result+=sprite_template(TEX_IDS[0],-12,-198,pmod)
        result+=sprite_template(TEX_IDS[1],-72,-264,pmod)
        result+=bytes.fromhex('ffff')
    assert len(result)==88
    return bytes(result)


def actor_template_bytes():
    # First 8 bytes (+00/+04) belong to the native allocator/list manager.
    # All following words are drawn from the proved slot-5 actor, except for
    # the new group address and the established -10Z placement correction.
    words=[0]*22
    def set_addr(addr, val):
        assert addr%4==0 and 8<=addr<96
        words[(addr-8)//4]=val
    set_addr(0x0C,DRAW_CALLBACK)
    set_addr(0x28,0xA7560000)
    set_addr(0x2C,2)
    set_addr(0x30,0xFFF9A950)
    set_addr(0x3C,GROUP)
    for k,v in zip((0x40,0x44,0x48),XYZ):set_addr(k,v<<16)
    set_addr(0x4C,0x17130570)
    return struct.pack('>22I',*words)


def patched_program(patched_prg:bytes):
    if len(patched_prg)!=37088:raise ValueError('Unexpected prepared program size')
    old=struct.unpack_from('>I',patched_prg,0x2D48)[0]
    if old!=ORIGINAL_INIT:raise ValueError(f'Initializer changed: {old:08X}')
    code,asm,literals=build_code()
    stage=bytearray(patched_prg)
    stage+=b'\0'*(END-STAGE-len(stage))
    assert stage[HOOK-STAGE:END-STAGE]==b'\0'*(END-HOOK)
    stage[HOOK-STAGE:HOOK-STAGE+len(code)]=code
    struct.pack_into('>I',stage,0x2D48,HOOK)
    stage[GROUP-STAGE:GROUP-STAGE+88]=group_bytes()
    stage[ACTOR_TEMPLATE-STAGE:ACTOR_TEMPLATE-STAGE+88]=actor_template_bytes()
    assert stage[FLAG-STAGE:FLAG-STAGE+8]==b'\0'*8
    assert len(stage)==END-STAGE
    return bytes(stage)

# ----------------- Auditable original-relative patch data -----------------
# These are hexadecimal byte changes applied to the uncompressed original
# Twin Seeds stage resources (offsets are relative to each PRS decoded file).
# They are ordinary, explicitly listed data, NOT Python instructions.
# Original-file and patched-file SHA-256 hashes guard against mismatch.
# Large RD7P block: indexed 4bpp palm artwork corrections reconstructed
# during restoration. Final 24x198 and 144x88 textures are still generated
# at patch time from the user-supplied original game disc.
# Editing a hunk intentionally requires recalculating its output SHA-256.
PATCH_MANIFEST = {
    "PRG7P": {
        "original_size": 37088,
        "patched_size": 37088,
        "input_sha256": "e20c209614f9ba1245bab5cb5916dbf503cb1eda0b00163375b0bdc4a5b06f81",
        "output_sha256": "dcdd19c60b4651e28548373dc6423d924c9af21206e1a3da913ce8aceb2563fa",
        "hunks": [
            # 0x04805: Trunk: restore original registration bytes for verified table input (9 bytes)
            (0x04805, "24 0c 90 00 24 18 90 03 f8"),
            # 0x04859: Canopy: restore original registration bytes for verified table input (9 bytes)
            (0x04859, "24 06 e0 00 24 0c 70 08 2c"),
        ],
    },
    "RD7P": {
        "original_size": 449804,
        "patched_size": 449804,
        "input_sha256": "e07f8522acca8c9203f58ba42e811fa53f16e633b9c8d37854c22e826c12a7ef",
        "output_sha256": "34ccf5522115b818d902225e8de1031f660248c368dccd0a115476eacae0ab5d",
        "hunks": [
            # 0x406D4: Palm canopy: restored 4bpp pixel-index data (3 bytes)
            (0x406D4, "ff ff ff"),
            # 0x406E4: Palm canopy: restored 4bpp pixel-index data (3 bytes)
            (0x406E4, "00 00 00"),
            # 0x406F3: Palm canopy: restored 4bpp pixel-index data (6 bytes)
            (0x406F3, "09 8d dd dd ff f0"),
            # 0x406FE: Palm canopy: restored 4bpp pixel-index data (11 bytes)
            (0x406FE, "00 00 00 00 00 00 00 0f f0 00 00"),
            # 0x4070E: Palm canopy: restored 4bpp pixel-index data (1226 bytes)
            (0x4070E, """
                0d 00 00 00 00 04 46 75 68 af de f0 00 00 00 00
                00 00 00 00 00 0f ff ed bf de 00 00 ff ff ff fd
                de e0 00 00 00 00 44 34 45 9b bd df f0 00 00 00
                00 00 00 00 00 0d b8 77 53 37 b0 0d ee bd 77 77
                78 a0 00 00 00 00 22 24 69 9a bb bb ff f0 00 00
                00 00 00 00 0f b8 77 a4 71 31 7b fd 78 77 33 33
                46 64 00 00 00 00 00 02 46 99 9c eb bc ff ff 00
                00 00 00 0f eb e7 fc ea a7 83 7f c8 13 21 21 22
                04 40 00 00 00 00 00 00 06 66 ab cd bd ce ff ff
                00 00 00 ec be 87 fd ba ab 97 79 54 11 11 11 02
                00 20 00 00 00 00 00 00 09 06 99 9a a9 cc ec dd
                f0 00 ee d9 aa 4f fd 0a 8a 95 65 32 02 21 00 00
                00 00 00 00 00 00 0f ff ed b8 b9 a9 99 b9 c9 b8
                ff 0e cd bb a9 ff ee a8 d9 66 66 42 00 01 00 00
                00 00 00 00 00 0f ed ed bb bb bb ad a5 99 b9 ea
                ad cd bb bb a8 ef eb 8b b8 66 34 44 00 00 00 00
                00 00 00 00 00 eb 78 98 86 ad a9 88 ea 99 aa bc
                6c bb ab bb 7c ff eb ab a6 96 44 00 00 00 00 00
                00 00 00 00 08 74 31 59 66 69 78 88 aa dd b9 bc
                bb 95 ba 85 7f fe db c6 96 96 60 00 00 00 00 00
                00 00 00 00 05 14 34 66 44 46 54 5a aa de cb 9f
                aa 69 89 5a bf ee cb b6 5a 96 40 00 00 00 00 00
                00 00 00 00 04 12 11 44 24 05 04 58 98 ad ce eb
                ba 6f 65 6a ee ed b8 86 46 66 00 00 00 00 00 00
                00 00 00 00 01 00 01 23 40 05 59 6b 57 9a cd ce
                df cc 86 59 ac fd a9 56 66 60 df ff 00 e0 00 00
                00 00 00 00 00 00 00 00 57 07 8f 87 a8 59 bd dc
                ee de a7 6b a9 cb a9 64 87 7a bb de de ff ff f0
                00 00 00 00 00 00 0a 8a b7 ff fd d9 bb a9 9d dc
                ed ee 8d 69 aa 9c a8 87 78 35 38 8b 77 d7 7b ff
                f0 00 00 00 00 0f ff b7 ff fe dd fe de ff fd dd
                dd ed fd 9b 99 9b bb b9 85 55 55 46 23 53 31 7b
                ff 00 00 00 0f fd 8e df bd dc dd dd de ee de ed
                ce de aa 99 ab ae aa 96 55 69 68 34 31 23 31 33
                7b f0 00 0f eb 87 3c 9b ba be ce dd dd de de ee
                fe ef 9a 9a b5 be a9 99 45 aa 69 44 42 22 43 13
                43 ba 00 df e7 87 6a ad eb 9c bc ad dc fd ce ee
                ef ee fd a9 b6 bb b9 89 55 36 5b aa 96 04 44 20
                33 25 0d ed 96 99 66 cb eb 9b bb ab ba dd dd ee
                ce ee cd a8 5c fd 87 78 57 96 77 57 90 04 42 20
                22 11 0c da 46 96 5a ca ea 9b 99 9a ab bb aa bb
                ed dd bb aa 8f ef 77 75 85 64 36 73 77 00 20 00
                00 12 a9 a6 06 59 8a ab c9 99 86 69 ba ad 98 bb
                ce dd ef bb ef eb ca a4 54 94 33 57 37 7d 00 00
                00 00 66 46 05 5c 69 96 69 96 9b bb bc fc 9b ef
                bf dd dd ff ef fa 99 96 66 66 64 4b 17 77 70 00
                00 00 66 46 05 5b 69 99 69 66 ab bb cb dd 9a ff
                ee dd dd dd df ca 96 64 69 0a 68 9d 33 38 69 00
                00 00 44 40 07 79 46 64 46 68 ee de ee eb 8c ce
                fd dd dd dd df d9 96 65 69 09 66 8c 31 38 58 00
                00 00 46 40 89 94 24 42 56 9b da db 9c ba 6a df
                de db cd cd df a6 66 9b 5a bb 77 8b 27 37 00 00
                00 00 44 07 86 93 04 41 36 6a f9 9a 9a 96 68 8f
                ab ea cb be ed e6 44 99 49 78 b9 9d 9b 33 50 00
                00 00 00 78 b4 40 02 05 34 bd 99 69 69 95 6b fe
                fd c9 bc be fe e9 96 66 64 35 86 ba 87 77 30 00
                00 00 00 7e 92 20 00 05 64 6b 9c 6a 96 95 8b ef
                ed ba db de ff fb 66 46 44 25 c8 98 63 bb 80 00
                00 00 08 89 61 00 35 66 63 5e 6b 99 64 64 8a ff
                d8 b6 ba cb ff eb 64 42 24 21 75 93 53 8a 8f 00
                00 00 05 66 10 00 04 46 6e ac 6a 96 44 44 58 fe
                bb 54 99 ce ff db 90 00 22 22 33 41 23 46 6a b0
                00 00 01 34 00 00 01 39 ab bf ab 99 40 21 3a eb
                74 12 a9 ab fb 8b 64 00 00 02 42 51 44 13 67 70
                00 00 00 33 00 00 00 69 cd df ba 60 40 00 29 b4
                33 02 98 aa fb 79 40 00 00 00 21 42 04 12 26 3b
                00 00 00 10 00 00 00 6a dc dd a9 40 40 00 36 93
                10 00 66 ba d8 54 40 00 00 00 21 20 02 11 05 3b
                00 00 00 00 00 00 00 6a aa eb 94 60 20 00 02 30
                00 00 66 da 87 34 00 00 00 00 00 00 00 24 05 77
                d0 00 00 00 00 00 05 09 8d ba 64 00 00 00 00 00
                00 00 00 da 74 42 00 00 00 00 00 00 00 04 04 43
                70 00 00 00 00 00 04 09 77 c9 60 00 00 00 00 00
                00 00 00 00 62 20 00 00 00 00 00 00 00 02 02 47
                77 00 00 00 00 00 00 64 75 66 40 00 00 00 00 00
                00 00 00 00 60 00 00 00 00 00 00 00 00 00 00 68
                83 00 00 00 00 00 00 04 35 30
            """),
            # 0x40BDD: Palm canopy: restored 4bpp pixel-index data (11 bytes)
            (0x40BDD, "00 00 00 00 00 50 00 00 00 00 00"),
            # 0x40BED: Palm texture: localized pixel-index correction (2 bytes)
            (0x40BED, "69 b3"),
            # 0x40BF5: Palm texture: localized pixel-index correction (3 bytes)
            (0x40BF5, "04 25 20"),
            # 0x40BFD: Palm texture: localized pixel-index correction (2 bytes)
            (0x40BFD, "00 00"),
            # 0x40C05: Palm texture: localized pixel-index correction (3 bytes)
            (0x40C05, "00 00 00"),
            # 0x40C0D: Palm texture: localized pixel-index correction (2 bytes)
            (0x40C0D, "06 97"),
            # 0x40C15: Palm texture: localized pixel-index correction (2 bytes)
            (0x40C15, "02 12"),
            # 0x40C1D: Palm texture: localized pixel-index correction (3 bytes)
            (0x40C1D, "00 00 00"),
            # 0x40C25: Palm texture: localized pixel-index correction (2 bytes)
            (0x40C25, "00 00"),
            # 0x40C2D: Palm texture: localized pixel-index correction (3 bytes)
            (0x40C2D, "04 08 70"),
            # 0x40C35: Palm texture: localized pixel-index correction (2 bytes)
            (0x40C35, "01 22"),
            # 0x40C3D: Palm texture: localized pixel-index correction (3 bytes)
            (0x40C3D, "00 00 00"),
            # 0x40C45: Palm texture: localized pixel-index correction (2 bytes)
            (0x40C45, "00 00"),
            # 0x40C4D: Palm texture: localized pixel-index correction (3 bytes)
            (0x40C4D, "04 06 70"),
        ],
    },
}

def patch_manifest():
    """Return the human-readable, hash-checked patch table above."""
    return PATCH_MANIFEST

def sha(x):return hashlib.sha256(x).hexdigest()


def apply_verified_hunks(raw:bytes, spec:dict):
    if sha(raw)!=spec['input_sha256'] or len(raw)!=spec['original_size']:
        raise ValueError('Resource content is not the expected original')
    b=bytearray(raw)
    b.extend(b'\0'*(spec['patched_size']-len(b)))
    for at,hexbytes in spec['hunks']:
        part=bytes.fromhex(hexbytes)
        if at+len(part)>len(b):raise ValueError('Invalid patch hunk bounds')
        b[at:at+len(part)]=part
    if sha(b)!=spec['output_sha256']:
        raise ValueError('Patch hunks failed offline source verification')
    return bytes(b)

# ---------------------- Fail-closed disc image writer ----------------------
def patch_sector(f, n, bytes_, off=0):
    f.seek(n*SECTOR_SIZE)
    raw=bytearray(f.read(SECTOR_SIZE))
    if len(raw)!=SECTOR_SIZE or raw[:12]!=b'\0'+b'\xff'*10+b'\0' or raw[15]!=1:
        raise ValueError(f'Invalid Mode1/2352 sector {n}')
    if off+len(bytes_)>2048:raise ValueError('Sector write crosses data boundary')
    raw[16+off:16+off+len(bytes_)]=bytes_
    fix_mode1(raw)
    f.seek(n*SECTOR_SIZE)
    f.write(raw)

def verify_sector_checksums(stream, sector_numbers):
    for n in sector_numbers:
        stream.seek(n * SECTOR_SIZE)
        original = stream.read(SECTOR_SIZE)
        if len(original) != SECTOR_SIZE or fix_mode1(bytearray(original)) != original:
            raise RuntimeError(f'Sector EDC/ECC readback mismatch: {n}')


def read_cue_text(cue: Path) -> str:
    try:
        return cue.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError:
        return cue.read_text(encoding='cp1252')


def patch_original_disc(cue: Path, *, dry_run: bool = False, check_only: bool = False) -> None:
    cue = cue.resolve()
    report = inspect(cue)
    print('NiGHTS Twin Seeds Palm Restoration')
    print('Original CUE:', cue)
    print('Source BIN:  ', report['data_bin'])
    print('CUE FILE entries:', report['cue_file_count'], '  Tracks:', report['track_count'])
    for r in report['resources']:
        print(' ', 'PASS' if r['matched'] else 'FAIL', r['resource'])
    if not report['supported_original']:
        raise UnsupportedImage('Unrecognized or already patched disc image: original resource hashes must match.')
    if report['cue_file_count'] != 1 or report['track_count'] != 21:
        raise UnsupportedImage('Requires a single FILE/BINARY CUE with the reviewed 21-track layout.')
    cue_text = read_cue_text(cue)
    # All source CUE references were validated by parse_cue(). No multi-BIN
    # support in this release, avoiding accidental data/audio track loss.
    file_re = re.compile(r'(?im)^(?P<indent>[ \t]*)FILE[ \t]+(?:"[^"\r\n]+"|[^\s\r\n]+)[ \t]+BINARY[ \t]*$')
    if len(file_re.findall(cue_text)) != 1:
        raise UnsupportedImage('Expected exactly one FILE ... BINARY record in CUE.')
    dest_bin = Path(report['data_bin']).with_name(OUTPUT_STEM + '.BIN')
    dest_cue = cue.with_name(OUTPUT_STEM + '.CUE')
    pending = dest_bin.with_name(dest_bin.name + '.partial')
    source_bin = Path(report['data_bin'])
    if any(p.resolve() == source_bin.resolve() for p in (dest_bin, dest_cue, pending)):
        raise UnsupportedImage('Output name conflicts with input BIN.')
    if check_only:
        print('SUPPORTED ORIGINAL IMAGE. No files changed (--check).')
        return
    if not dry_run and any(p.exists() for p in (dest_bin, dest_cue, pending)):
        raise FileExistsError('Output NIGHTS_PATCHED.BIN/CUE already exists. Move the pair before retrying; no overwrite is permitted.')

    manifest = patch_manifest()
    prepared = []
    with source_bin.open('rb') as stream:
        # RD7PH stage geometry is deliberately untouched: no fixed 3D tree.
        for name in ('PRG7P', 'RD7P'):
            entry = next(e for e in KNOWN if e['resource'] == name + '.PRS')
            source = read_iso_resource(stream, entry)
            plain = decompress(source)
            verified = apply_verified_hunks(plain, manifest[name])
            patched = (patched_program(patch_registration_table(verified))
                       if name == 'PRG7P' else append_scaled_art(verified))
            packed = compress(patched)
            if decompress(packed) != patched:
                raise ValueError(name + '.PRS compression round-trip failed')
            if len(packed) > entry['bytes']:
                raise ValueError(f'{name}.PRS exceeds original allocated space: {len(packed)} > {entry["bytes"]}')
            prepared.append((entry, packed.ljust(entry['bytes'], b'\0'), sha(patched)))
            print(f'Prepared {name}.PRS: {len(packed):,} / {entry["bytes"]:,} bytes; decoded SHA-256 {sha(patched)}')
    assert hashlib.sha256(group_bytes()).hexdigest() == '6f76e124e6ab4da29d30813577f720fe7a936fd8a05608ad1c9e255234872d7d', 'Anchor regression'
    print('Native billboards: trunk 24x198; canopy 144x88; XYZ (3784, 4801, 5626)')
    print('Original RD7PH geometry preserved; output has no fixed-polygon palm.')
    if dry_run:
        print('DRY RUN COMPLETE. No files created.')
        return

    updated_cue = file_re.sub(lambda m: m.group('indent') + 'FILE "' + dest_bin.name + '" BINARY', cue_text, count=1)
    print('Writing:', dest_bin)
    try:
        with source_bin.open('rb') as original, pending.open('xb') as output:
            shutil.copyfileobj(original, output, 8 * 1024 * 1024)
        changed = []
        with pending.open('r+b') as output:
            for entry, payload, decoded_hash in prepared:
                for i in range((entry['bytes'] + 2047) // 2048):
                    patch_sector(output, entry['sector'] + i, payload[i*2048:(i+1)*2048])
                    changed.append(entry['sector'] + i)
            output.flush()
            os.fsync(output.fileno())
        with pending.open('rb') as output:
            for entry, payload, _ in prepared:
                if read_iso_resource(output, entry) != payload:
                    raise RuntimeError('Resource readback verification failed: ' + entry['resource'])
            verify_sector_checksums(output, changed)
        dest_cue.write_text(updated_cue, encoding='utf-8')
        pending.rename(dest_bin)
    except BaseException:
        if pending.exists():
            pending.unlink()
        if dest_cue.exists() and not dest_bin.exists():
            dest_cue.unlink()
        raise
    print('SUCCESS. Patched image:', dest_cue)
    print('Both files must stay together. See README.txt before renaming them to NIGHTS.BIN/CUE.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cue', type=Path, help='Path to ORIGINAL single-BIN NIGHTS.CUE')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='Only verify the original game image')
    group.add_argument('--dry-run', action='store_true', help='Prepare/verify the patch without writing files')
    args = parser.parse_args()
    try:
        patch_original_disc(args.cue, dry_run=args.dry_run, check_only=args.check)
    except (ValueError, OSError, UnsupportedImage, AssertionError, RuntimeError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
