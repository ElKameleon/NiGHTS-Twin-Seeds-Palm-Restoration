# NiGHTS into Dreams — Twin Seeds Palm Tree Restoration

## What It Does

Restores a missing palm tree to Twin Seeds using NiGHTS' original billboard sprite system.

Tested successfully in Mednafen and on real Sega Saturn hardware. Tree collision is not currently implemented.

## Compatibility

**At this time, only the NTSC version of NiGHTS into Dreams is supported. PAL versions are not supported.**

The patcher checks your game image for compatibility before making any changes.

## How It Works

The patcher uses artwork from your original game image and modifies the Twin Seeds stage to spawn the restored palm automatically.

Your original game files are left untouched.

## How to Install

Requires Windows and Python 3. No additional Python packages are needed.

1. Extract all three files from the ZIP.
2. Drag your original `NIGHTS.CUE` onto `Patch_NiGHTS_Palm.bat`.
3. The patcher creates `NIGHTS_PATCHED.BIN` and `NIGHTS_PATCHED.CUE` alongside your originals.
4. Load or burn `NIGHTS_PATCHED.CUE` to play.

## Optional: Renaming the Files

The patched files can be used as-is. Alternatively, rename them to `NIGHTS.BIN` and `NIGHTS.CUE` after moving them away from your originals.

If you rename them, open the CUE file in a text editor and change `NIGHTS_PATCHED.BIN` to `NIGHTS.BIN`.

---

Unofficial fan restoration. Not affiliated with or endorsed by Sega. No original game files are included. You must provide your own game image.