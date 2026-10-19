#!/usr/bin/env python3
"""NiGHTS into Dreams (Sega Saturn) - Twin Seeds Palm Tree Restoration v1.1.

Patch a verified original NTSC 21-track single-BIN/CUE image without modifying
its source files. Requires Python 3; no external modules or game assets.

This patch reconstructs the canopy pixels by losslessly exchanging the two
halves of each original row. The trunk and canopy keep their original texture
resolutions (24x248 and 64x44). NiGHTS' native VDP1 renderer scales them
independently using a palm-specific two-pass sprite callback.

The compressed stage program grows by 18 bytes but remains inside its original
10 CD sectors. The script updates that file's ISO9660 directory length; it
never moves files or changes the CUE track layout.

Run:
    py -3 Patch_NiGHTS_Palm.py "NIGHTS.CUE"
    py -3 Patch_NiGHTS_Palm.py "NIGHTS.CUE" --check
    py -3 Patch_NiGHTS_Palm.py "NIGHTS.CUE" --dry-run
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


def require(cond, msg):
    # Explicit check that survives python -O (unlike assert).
    if not cond:
        raise ValueError(msg)

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
        'release_status':'Native billboard restoration v1.1 (native textures)',
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

# ------------- Native texture recovery (no resampling, no new pixels) ------
TRUNK_SOURCE_RD = 0x40C80


CANOPY_SOURCE_RD = 0x406D0


TRUNK_SOURCE_DIMS = (24, 248)


CANOPY_SOURCE_DIMS = (64, 44)


RD7P_ORIG_SIZE = 449804


# The original 64x44 canopy is stored on disc with the two 32-pixel
# horizontal halves of every scanline exchanged. Each 4bpp half is 16
# bytes. This reversible transform recreates the approved pixels from the disc.
RD7P_RECOVERED_SHA256 = '34ccf5522115b818d902225e8de1031f660248c368dccd0a115476eacae0ab5d'

def recover_canopy_half_rows(rd7p_original: bytes) -> bytes:
    if len(rd7p_original) != RD7P_ORIG_SIZE:
        raise ValueError('Unexpected original RD7P plain size')
    result = bytearray(rd7p_original)
    row_bytes = CANOPY_SOURCE_DIMS[0] // 2  # 64 pixels @ 4bpp = 32 bytes
    half = row_bytes // 2  # 16 bytes = 32 pixels
    for y in range(CANOPY_SOURCE_DIMS[1]):
        offset = CANOPY_SOURCE_RD + y * row_bytes
        original_row = rd7p_original[offset:offset + row_bytes]
        result[offset:offset + row_bytes] = original_row[half:] + original_row[:half]
    recovered = bytes(result)
    if sha(recovered) != RD7P_RECOVERED_SHA256:
        raise ValueError('Original canopy recovery did not match verified restoration')
    return recovered

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


# ---- v1.1: two-pass native-scale wrapper ----------------------------------
WRAP=0x060C9300           # wrapper callback, between HOOK code and FLAG


RENDER_BODY=0x06020912    # perspective sprite renderer, AFTER its scale-slot resets


MATRIX_PUSH=0x0600CA24


MATRIX_TRANSLATE=0x0600CBDA


MATRIX_POP=0x0600CA4E


RENDERER_ENTRY=0x060208FA  # original callback's entry (hard-codes scale 1.0)


# Original callback, verified byte-for-byte before the wrapper replaces it for the palm.
ORIGINAL_CALLBACK_WORDS=(0x4F22,0xD006,0x400B,0x0009,0x541C,0x551D,0xD005,0x400B,0x561E,0xD004,0x400B,0x0009,0xD004,0x402B,0x4F26)


# Per part: native texture size, sprite offset (native units), X scale and Y factor (8.8).
# Final rectangle: x = dx*Sx, y = dy*Sx*Sy, scaled again by distance.
PALM_PARTS=(
    dict(name='trunk', tex=TEX_IDS[0], size=(24,248), dx=-12, dy=-248, sx=0x100, sy=0x0CC),
    dict(name='canopy',tex=TEX_IDS[1], size=(64,44),  dx=-32, dy=-132, sx=0x240, sy=0x0E3),
)


GROUP_SHA256='6d8b04001071d75b875e03e3581c8d4230259ffd91139c04f8e123bd0773c3ab'
HOOK_SHA256='993541c203a887efd71d43aa4f4e45ff30fc0341c0930f3dae558690fb8159e1'
WRAPPER_SHA256='04ebef0bb63898f1f9aabd79384d5e6eddbf62ad17398ea882339036713ba26c'


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


def build_wrapper():
    """Palm-only draw callback: the original push/translate/pop sequence, but the
    renderer body runs twice (trunk list, then canopy list), each with its own scale.
    Frame selector (actor+0x24), r1 and the shared scale slots are saved and restored."""
    asm=SH2(WRAP)
    asm.emit(0x4F22)  # sts.l pr,@-r15
    asm.emit(0x2F16)  # mov.l r1,@-r15
    asm.emit(0x851A)  # mov.w @(20,r1),r0   (frame selector, actor+0x24)
    asm.emit(0x2F06)  # mov.l r0,@-r15
    asm.emit(0xC532)  # mov.w @(100,gbr),r0 (shared X scale slot)
    asm.emit(0x2F06)
    asm.emit(0xC533)  # mov.w @(102,gbr),r0 (shared Y factor slot)
    asm.emit(0x2F06)
    # identical to the original callback up to the renderer call
    asm.lit(0,'push'); asm.emit(0x400B); asm.emit(0x0009)   # jsr push matrix; nop
    asm.emit(0x541C); asm.emit(0x551D)                        # r4=[r1+0x30], r5=[r1+0x34]
    asm.lit(0,'xlate'); asm.emit(0x400B); asm.emit(0x561E)   # jsr translate; r6=[r1+0x38]
    for sel in (0,1):
        asm.emit(0x51F3)         # mov.l @(12,r15),r1  (re-read saved r1)
        asm.emit(0xE000|sel)     # mov #sel,r0
        asm.emit(0x811A)         # mov.w r0,@(20,r1)   (select group list)
        asm.lit(0,'sx%d'%sel); asm.emit(0xC132)   # mov.w r0,@(100,gbr)
        asm.lit(0,'sy%d'%sel); asm.emit(0xC133)   # mov.w r0,@(102,gbr)
        asm.lit(0,'render'); asm.emit(0x400B); asm.emit(0x0009)
        if sel==0:
            # Culled this frame (bit 15 of actor flag word set by the renderer)?
            # Then skip pass 2 so the culled path runs once, exactly as in v1.0.1.
            asm.emit(0x51F3)     # mov.l @(12,r15),r1
            asm.emit(0x851F)     # mov.w @(30,r1),r0
            asm.emit(0x4011)     # cmp/pz r0   (T=0 when culled)
            asm.branch('bf','done')
    asm.label('done')
    asm.emit(0x60F6); asm.emit(0xC133)   # restore Y factor slot
    asm.emit(0x60F6); asm.emit(0xC132)   # restore X scale slot
    asm.emit(0x51F1)                      # mov.l @(4,r15),r1
    asm.emit(0x60F6); asm.emit(0x811A)   # restore frame selector
    asm.emit(0x61F6)                      # mov.l @r15+,r1
    asm.lit(0,'pop'); asm.emit(0x402B); asm.emit(0x4F26)     # jmp pop matrix; lds.l @r15+,pr
    consts={'push':MATRIX_PUSH,'xlate':MATRIX_TRANSLATE,'render':RENDER_BODY,'pop':MATRIX_POP}
    for sel,part in enumerate(PALM_PARTS):
        consts['sx%d'%sel]=part['sx']; consts['sy%d'%sel]=part['sy']
    code,loc=asm.code(consts)
    require(WRAP+len(code)<=FLAG,'wrapper overruns FLAG')
    return code,loc


def verify_original_callback(prg):
    off=DRAW_CALLBACK-STAGE
    require(struct.unpack_from('>15H',prg,off)==ORIGINAL_CALLBACK_WORDS,'Stationary draw callback differs from the reviewed original')
    pool=struct.unpack_from('>4I',prg,off+0x1E)
    require(pool==(MATRIX_PUSH,MATRIX_TRANSLATE,RENDERER_ENTRY,MATRIX_POP),'Draw callback literals differ from the reviewed original')


def sprite_template(tex_id,dx,dy,pmod):
    return struct.pack('>HhhHH5H',tex_id,dx,dy,pmod,1,0,0,0,0,0)


def group_bytes():
    # Two single-sprite lists, selected by the frame word at actor+0x24:
    # list 0 = trunk, list 1 = canopy (native textures, native offsets).
    # The wrapper draws list 0 then list 1, so draw order matches v1.0.1.
    lists=[]
    for part in PALM_PARTS:
        lists.append(sprite_template(part['tex'],part['dx'],part['dy'],0x1088)+bytes.fromhex('ffff'))
    result=bytearray(struct.pack('>HH',4,4+len(lists[0])))
    for l in lists: result+=l
    require(len(result)==48,'unexpected group size')
    result+=b'\0'*(88-len(result))
    return bytes(result)


def actor_template_bytes():
    # First 8 bytes (+00/+04) belong to the native allocator/list manager.
    # All following words are drawn from the proved slot-5 actor, except for
    # the new group address and the established -10Z placement correction.
    words=[0]*22
    def set_addr(addr, val):
        assert addr%4==0 and 8<=addr<96
        words[(addr-8)//4]=val
    set_addr(0x0C,WRAP)
    set_addr(0x28,0xA7560000)
    set_addr(0x2C,2)
    set_addr(0x30,0xFFF9A950)
    set_addr(0x3C,GROUP)
    for k,v in zip((0x40,0x44,0x48),XYZ):set_addr(k,v<<16)
    set_addr(0x4C,0x17130570)
    return struct.pack('>22I',*words)


def patched_program(patched_prg:bytes):
    require(len(patched_prg)==37088,'Unexpected prepared program size')
    old=struct.unpack_from('>I',patched_prg,0x2D48)[0]
    require(old==ORIGINAL_INIT,f'Initializer changed: {old:08X}')
    verify_original_callback(patched_prg)
    # The registration table must already hold the native-size entries
    # (restored by the verified hunks below); v1.1 does not repoint them.
    require(patched_prg[0x4804:0x4810]==struct.pack('>IIHH',0x00240C90,0x00241890,0x03F8,1),'Trunk registration is not the native entry')
    require(patched_prg[0x4858:0x4864]==struct.pack('>IIHH',0x002406E0,0x00240C70,0x082C,1),'Canopy registration is not the native entry')
    code,asm,literals=build_code()
    wcode,wlit=build_wrapper()
    require(HOOK+len(code)<=WRAP,'startup hook overruns the wrapper')
    stage=bytearray(patched_prg)
    stage+=b'\0'*(END-STAGE-len(stage))
    require(stage[HOOK-STAGE:END-STAGE]==b'\0'*(END-HOOK),'Hook region is not blank')
    stage[HOOK-STAGE:HOOK-STAGE+len(code)]=code
    stage[WRAP-STAGE:WRAP-STAGE+len(wcode)]=wcode
    struct.pack_into('>I',stage,0x2D48,HOOK)
    stage[GROUP-STAGE:GROUP-STAGE+88]=group_bytes()
    stage[ACTOR_TEMPLATE-STAGE:ACTOR_TEMPLATE-STAGE+88]=actor_template_bytes()
    require(stage[FLAG-STAGE:FLAG-STAGE+8]==b'\0'*8,'Flag area not blank')
    require(len(stage)==END-STAGE,'Unexpected stage size')
    require(sha(code)==HOOK_SHA256 and sha(wcode)==WRAPPER_SHA256 and sha(group_bytes())==GROUP_SHA256,'Generated code/group differ from the reviewed build')
    return bytes(stage)

# --------- Two small, auditable stage registration corrections -------------
# No texture pixel data is embedded in this source. The original canopy
# pixel bytes are recovered with recover_canopy_half_rows() above.
# The 18 bytes below are stage sprite registration values, NOT image pixels.
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
}

def patch_manifest():
    """Return the verified, human-readable PRG7P registration changes."""
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

def read_iso_sector(f, n):
    f.seek(n*SECTOR_SIZE)
    raw=f.read(SECTOR_SIZE)
    if len(raw)!=SECTOR_SIZE or raw[:12]!=SYNC or raw[15]!=1:
        raise UnsupportedImage(f'Invalid MODE1/2352 sector {n}')
    return raw[DATA_START:DATA_START+2048]


def iso_files(f, max_dirs=512):
    """Walk the ISO9660 tree; return [(path, first_lba, size)] for every file."""
    pvd=read_iso_sector(f,16)
    rec=pvd[156:190]
    queue=[(struct.unpack_from('<I',rec,2)[0],struct.unpack_from('<I',rec,10)[0],'')]
    seen=set(); files=[]
    while queue and len(seen)<max_dirs:
        lba,length,path=queue.pop(0)
        if lba in seen: continue
        seen.add(lba)
        data=b''.join(read_iso_sector(f,lba+i) for i in range((length+2047)//2048))[:length]
        pos=0
        while pos<len(data):
            n=data[pos]
            if n==0:
                pos=(pos//2048+1)*2048; continue
            r=data[pos:pos+n]; pos+=n
            if len(r)<34 or len(r)<33+r[32]: break
            ext=struct.unpack_from('<I',r,2)[0]; size=struct.unpack_from('<I',r,10)[0]
            name=r[33:33+r[32]]
            if name in (b'\x00',b'\x01'): continue
            nm=name.decode('ascii','replace')
            if r[25]&2: queue.append((ext,size,path+nm+'/'))
            else: files.append((path+nm,ext,size))
    return files


# The v1.1 wrapper enters the resident renderer mid-function and relies on the
# position routines, so the executable's code window must be the reviewed one.
# (Hashes only; no executable bytes are stored here.)
EXE_WINDOW_OFFSET = 0x1B000   # load base 0x06004000 -> 0x0601F000
EXE_WINDOW_PARTS = (
    (0x0000, 0x1800, '4224ac718a07030b2ebd1210b5c83e684006ce8093b0c5e3fe3edcb63e96df6a'),  # 0x0601F000-0x060207FF
    (0x1800, 0x0800, '55ff6eda7b522c4e09f0fcbe9727a41c719e9ca31b73496a95e61cbb0f3ab42e'),  # 0x06020800-0x06020FFF
)
EXE_WINDOW_LEN = 0x2000


def verify_executable(f):
    best=None
    for path,lba,size in iso_files(f):
        if size < EXE_WINDOW_OFFSET+EXE_WINDOW_LEN: continue
        first=EXE_WINDOW_OFFSET//2048
        window=b''.join(read_iso_sector(f,lba+first+i) for i in range(EXE_WINDOW_LEN//2048))
        hashes=[sha(window[o:o+n]) for o,n,_ in EXE_WINDOW_PARTS]
        if all(h==e for h,(_,_,e) in zip(hashes,EXE_WINDOW_PARTS)):
            return path
        if best is None: best=(path,hashes)
    detail='' if best is None else f' (first candidate {best[0]}: window hashes {best[1]})'
    raise UnsupportedImage('Could not find an executable whose renderer code window matches the reviewed one; '
                           'v1.1 depends on it.'+detail)


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


def find_prg_iso_record(stream):
    """Locate the one ORIGINAL PRG7P.PRS ISO9660 directory entry.

    The original file is 20,089 bytes but already occupies ten 2,048-byte
    sectors (20,480 bytes). A slightly longer PRS can fit in those SAME
    sectors, provided its actual length is updated in the ISO directory.

    Returns (directory_sector_lba, offset_in_sector). Fail closed if the ISO
    file table differs from the reviewed layout; do not search arbitrary bytes.
    """
    pvd = read_iso_sector(stream, 16)
    require(pvd[0] == 1 and pvd[1:6] == b'CD001', 'Bad ISO9660 primary descriptor')
    root = pvd[156:156 + pvd[156]]
    require(len(root) >= 34, 'Bad ISO9660 root record')
    root_lba = struct.unpack_from('<I', root, 2)[0]
    root_size = struct.unpack_from('<I', root, 10)[0]
    queue = [(root_lba, root_size, '')]
    visited = set()
    found = []
    while queue:
        lba, size, prefix = queue.pop(0)
        if lba in visited:
            continue
        visited.add(lba)
        require(len(visited) <= 512, 'ISO9660 directory recursion limit')
        data = b''.join(read_iso_sector(stream, lba + i) for i in range((size + 2047) // 2048))[:size]
        pos = 0
        while pos < len(data):
            length = data[pos]
            if length == 0:
                pos = (pos // 2048 + 1) * 2048
                continue
            require(length >= 34 and pos % 2048 + length <= 2048 and pos + length <= len(data),
                    'Malformed ISO9660 directory record')
            record = data[pos:pos+length]
            namelen = record[32]
            require(33 + namelen <= length, 'Bad ISO9660 filename length')
            name = record[33:33+namelen]
            extent = struct.unpack_from('<I', record, 2)[0]
            actual_size = struct.unpack_from('<I', record, 10)[0]
            require(extent == struct.unpack_from('>I', record, 6)[0] and
                    actual_size == struct.unpack_from('>I', record, 14)[0],
                    'Inconsistent ISO9660 little-/big-endian record')
            if name not in (b'\x00', b'\x01'):
                full = prefix + name.decode('ascii', 'replace')
                if record[25] & 2:
                    queue.append((extent, actual_size, full + '/'))
                elif name.split(b';')[0].upper() == b'PRG7P.PRS':
                    require(extent == 384 and actual_size == 20089,
                            'PRG7P.PRS extent/size differs from verified original')
                    found.append((lba + pos // 2048, pos % 2048))
            pos += length
    require(len(found) == 1, 'Expected exactly one original PRG7P.PRS ISO9660 directory entry')
    return found[0]


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
        with source_bin.open('rb') as stream:
            exe_name=verify_executable(stream)
        print('  PASS executable renderer code window:', exe_name)
        print('SUPPORTED ORIGINAL IMAGE. No files changed (--check).')
        return
    if not dry_run and any(p.exists() for p in (dest_bin, dest_cue, pending)):
        raise FileExistsError('Output NIGHTS_PATCHED.BIN/CUE already exists. Move the pair before retrying; no overwrite is permitted.')

    manifest = patch_manifest()
    prepared = []
    with source_bin.open('rb') as stream:
        print('  PASS executable renderer code window:', verify_executable(stream))
        directory_sector, directory_offset = find_prg_iso_record(stream)
        print(f'  PASS PRG7P.PRS ISO9660 directory entry (sector {directory_sector})')
        # RD7PH stage geometry is deliberately untouched: no fixed 3D tree.
        for name in ('PRG7P', 'RD7P'):
            entry = next(e for e in KNOWN if e['resource'] == name + '.PRS')
            source = read_iso_resource(stream, entry)
            plain = decompress(source)
            if name == 'PRG7P':
                verified = apply_verified_hunks(plain, manifest[name])
                patched = patched_program(verified)
            else:
                # Refuse anything other than the verified original release.
                if len(plain) != RD7P_ORIG_SIZE or sha(plain) != 'e07f8522acca8c9203f58ba42e811fa53f16e633b9c8d37854c22e826c12a7ef':
                    raise ValueError('RD7P.PRS content is not the verified original')
                patched = recover_canopy_half_rows(plain)
            packed = compress(patched)
            if decompress(packed) != patched:
                raise ValueError(name + '.PRS compression round-trip failed')
            sector_count = (entry['bytes'] + 2047) // 2048
            sector_capacity = sector_count * 2048
            # Other resources must still fit their declared file lengths.
            # PRG7P alone may grow into its existing final sector, with the
            # ISO9660 directory entry updated to the new exact byte length.
            if len(packed) > entry['bytes'] and (name != 'PRG7P' or len(packed) > sector_capacity):
                raise ValueError(f'{name}.PRS exceeds its original allocation: '
                                 f'{len(packed)} > {entry["bytes"]} (sector capacity {sector_capacity})')
            prepared.append((entry, packed.ljust(sector_capacity, b'\0'), sha(patched), len(packed)))
            print(f'Prepared {name}.PRS: {len(packed):,} bytes '
                  f'(original file {entry["bytes"]:,}; existing sectors {sector_capacity:,}); '
                  f'decoded SHA-256 {sha(patched)}')
            if name == 'PRG7P' and len(packed) > entry['bytes']:
                print('  PRG7P grows within its EXISTING ten sectors; ISO9660 file length will be updated.')
    require(sha(group_bytes())==GROUP_SHA256,'Group regression')
    print('Native billboards (no resampling): trunk 24x248 -> drawn 24x198; canopy 64x44 -> drawn 144x88')
    print('Two-pass wrapper at %08X; XYZ (3784, 4801, 5626)' % WRAP)
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
            for entry, payload, decoded_hash, packed_length in prepared:
                for i in range((entry['bytes'] + 2047) // 2048):
                    patch_sector(output, entry['sector'] + i, payload[i*2048:(i+1)*2048])
                    changed.append(entry['sector'] + i)
                if entry['resource'] == 'PRG7P.PRS' and packed_length > entry['bytes']:
                    # ISO9660 directory record: +10 size LE, +14 size BE.
                    # Same sectors and LBA; no TOC or track offsets change.
                    patch_sector(output, directory_sector, struct.pack('<I', packed_length)
                                 + struct.pack('>I', packed_length), off=directory_offset + 10)
                    changed.append(directory_sector)
            output.flush()
            os.fsync(output.fileno())
        with pending.open('rb') as output:
            for entry, payload, decoded_hash, packed_length in prepared:
                n = (entry['bytes'] + 2047) // 2048
                actual = b''.join(read_iso_sector(output, entry['sector'] + i) for i in range(n))
                if actual != payload:
                    raise RuntimeError('Resource sector readback verification failed: ' + entry['resource'])
                if sha(decompress(actual[:packed_length])) != decoded_hash:
                    raise RuntimeError('PRS round-trip readback verification failed: ' + entry['resource'])
            matching = [(p, lba, size) for p, lba, size in iso_files(output)
                        if p.split('/')[-1].split(';')[0].upper() == 'PRG7P.PRS']
            expected_len = next(length for entry, _, _, length in prepared if entry['resource'] == 'PRG7P.PRS')
            require(len(matching) == 1 and matching[0][1:] == (384, max(20089, expected_len)),
                    'Patched PRG7P.PRS ISO9660 directory size readback differs from expected')
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
    parser.add_argument('cue', type=Path, nargs='?', help='Path to ORIGINAL single-BIN NIGHTS.CUE')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='Only verify the original game image')
    group.add_argument('--dry-run', action='store_true', help='Prepare/verify the patch without writing files')
    args = parser.parse_args()
    if args.cue is None:
        parser.error('the path to the original NIGHTS.CUE is required')
    try:
        patch_original_disc(args.cue, dry_run=args.dry_run, check_only=args.check)
    except (ValueError, OSError, UnsupportedImage, AssertionError, RuntimeError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
