#requires -Version 5.1
# Deterministic Windows asset conversion. The checked-in source image is never edited.
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -ne 'Desktop') { throw 'Run icon conversion with Windows PowerShell 5.1.' }
$source = Join-Path $PSScriptRoot 'MirrorlyIcon.Source.png'
$assets = Join-Path (Split-Path $PSScriptRoot -Parent) 'Mirrorly.Desktop/Assets'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Missing approved icon source.' }
if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne
    '4F5C7DC84ABE54B7469E8DF53DF30FBF8125F17FADA74CE8D9A02ED8B70BA817') {
    throw 'Approved Mirrorly v1 icon source changed.'
}
if (-not ('MirrorlyIconAssets' -as [type])) {
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;

public static class MirrorlyIconAssets
{
    public static void Build(string sourcePath, string assetsPath)
    {
        using (var original = new Bitmap(sourcePath))
        {
            if (original.Width != original.Height || original.Width < 256)
                throw new InvalidOperationException("Icon source must be square and at least 256 pixels.");
            using (var prepared = new Bitmap(original.Width, original.Height, PixelFormat.Format32bppArgb))
            {
                using (var graphics = Graphics.FromImage(prepared))
                {
                    graphics.CompositingMode = CompositingMode.SourceCopy;
                    graphics.DrawImageUnscaled(original, 0, 0);
                }
                // Only the connected near-white area OUTSIDE the pale green rounded
                // square is made transparent. Interior pixels retain their source RGB.
                RemoveConnectedExterior(prepared);
                var sizes = new int[] { 16, 24, 32, 48, 256 };
                var frames = new List<byte[]>();
                foreach (int size in sizes)
                {
                    using (var scaled = Scale(prepared, size, size))
                    {
                        var file = Path.Combine(assetsPath, "AppIcon" + size + ".png");
                        scaled.Save(file, ImageFormat.Png);
                        using (var stream = new MemoryStream())
                        {
                            scaled.Save(stream, ImageFormat.Png);
                            frames.Add(stream.ToArray());
                        }
                    }
                }
                WriteIcon(Path.Combine(assetsPath, "Mirrorly.ico"), sizes, frames);
                WriteSquare(prepared, assetsPath, "StoreLogo.png", 50);
                WriteSquare(prepared, assetsPath, "LockScreenLogo.scale-200.png", 48);
                WriteSquare(prepared, assetsPath, "Square44x44Logo.targetsize-24_altform-unplated.png", 24);
                WriteSquare(prepared, assetsPath, "Square44x44Logo.scale-200.png", 88);
                WriteSquare(prepared, assetsPath, "Square150x150Logo.scale-200.png", 300);
                WriteCentered(prepared, assetsPath, "Wide310x150Logo.scale-200.png", 620, 300, 300);
                WriteCentered(prepared, assetsPath, "SplashScreen.scale-200.png", 1240, 600, 300);
            }
        }
    }

    private static void RemoveConnectedExterior(Bitmap bitmap)
    {
        int width = bitmap.Width, height = bitmap.Height;
        var rect = new Rectangle(0, 0, width, height);
        BitmapData data = bitmap.LockBits(rect, ImageLockMode.ReadWrite, PixelFormat.Format32bppArgb);
        try
        {
            int stride = data.Stride;
            var pixels = new byte[stride * height];
            Marshal.Copy(data.Scan0, pixels, 0, pixels.Length);
            var seen = new bool[width * height];
            var queue = new int[width * height];
            int head = 0, tail = 0;
            for (int x = 0; x < width; x++) { Enqueue(x, 0, width, stride, pixels, seen, queue, ref tail); Enqueue(x, height - 1, width, stride, pixels, seen, queue, ref tail); }
            for (int y = 1; y < height - 1; y++) { Enqueue(0, y, width, stride, pixels, seen, queue, ref tail); Enqueue(width - 1, y, width, stride, pixels, seen, queue, ref tail); }
            while (head < tail)
            {
                int id = queue[head++], x = id % width, y = id / width;
                int offset = y * stride + x * 4;
                int greenMinusBlue = pixels[offset + 1] - pixels[offset];
                int alpha = Math.Max(0, Math.Min(255, (greenMinusBlue - 1) * 255 / 22));
                pixels[offset + 3] = (byte)alpha;
                if (x > 0) Enqueue(x - 1, y, width, stride, pixels, seen, queue, ref tail);
                if (x + 1 < width) Enqueue(x + 1, y, width, stride, pixels, seen, queue, ref tail);
                if (y > 0) Enqueue(x, y - 1, width, stride, pixels, seen, queue, ref tail);
                if (y + 1 < height) Enqueue(x, y + 1, width, stride, pixels, seen, queue, ref tail);
            }
            // The source's white outer corners contain isolated antialias/noise
            // pixels. Restrict each row to the actual colored rounded-square span;
            // otherwise those disconnected white pixels survive as corner specks.
            for (int y = 0; y < height; y++)
            {
                int left = width, right = -1;
                for (int x = 0; x < width; x++)
                {
                    int offset = y * stride + x * 4;
                    if (pixels[offset + 1] - pixels[offset] >= 21 && pixels[offset + 2] < 250)
                    {
                        if (x < left) left = x;
                        right = x;
                    }
                }
                for (int x = 0; x < left; x++) pixels[y * stride + x * 4 + 3] = 0;
                for (int x = right + 1; x < width; x++) pixels[y * stride + x * 4 + 3] = 0;
            }
            Marshal.Copy(pixels, 0, data.Scan0, pixels.Length);
        }
        finally { bitmap.UnlockBits(data); }
    }

    private static void Enqueue(int x, int y, int width, int stride, byte[] pixels,
        bool[] seen, int[] queue, ref int tail)
    {
        int id = y * width + x;
        if (seen[id]) return;
        seen[id] = true;
        int offset = y * stride + x * 4;
        int b = pixels[offset], g = pixels[offset + 1], r = pixels[offset + 2];
        if (r >= 225 && g >= 235 && b >= 220 && g - b <= 20)
            queue[tail++] = id;
    }

    private static Bitmap Scale(Bitmap source, int width, int height)
    {
        var result = new Bitmap(width, height, PixelFormat.Format32bppArgb);
        using (var graphics = Graphics.FromImage(result))
        {
            graphics.Clear(Color.Transparent);
            graphics.CompositingMode = CompositingMode.SourceOver;
            graphics.CompositingQuality = CompositingQuality.HighQuality;
            graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
            graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            graphics.DrawImage(source, new Rectangle(0, 0, width, height), 0, 0,
                source.Width, source.Height, GraphicsUnit.Pixel);
        }
        return result;
    }

    private static void WriteSquare(Bitmap source, string dir, string name, int size)
    {
        using (var image = Scale(source, size, size)) image.Save(Path.Combine(dir, name), ImageFormat.Png);
    }

    private static void WriteCentered(Bitmap source, string dir, string name, int width, int height, int iconSize)
    {
        using (var image = new Bitmap(width, height, PixelFormat.Format32bppArgb))
        using (var icon = Scale(source, iconSize, iconSize))
        {
            using (var graphics = Graphics.FromImage(image))
            {
                graphics.Clear(Color.Transparent);
                graphics.DrawImageUnscaled(icon, (width - iconSize) / 2, (height - iconSize) / 2);
            }
            image.Save(Path.Combine(dir, name), ImageFormat.Png);
        }
    }

    private static void WriteIcon(string path, int[] sizes, List<byte[]> images)
    {
        using (var writer = new BinaryWriter(File.Create(path)))
        {
            writer.Write((ushort)0); writer.Write((ushort)1); writer.Write((ushort)sizes.Length);
            int offset = 6 + 16 * sizes.Length;
            for (int i = 0; i < sizes.Length; i++)
            {
                writer.Write((byte)(sizes[i] == 256 ? 0 : sizes[i]));
                writer.Write((byte)(sizes[i] == 256 ? 0 : sizes[i]));
                writer.Write((byte)0); writer.Write((byte)0);
                writer.Write((ushort)1); writer.Write((ushort)32);
                writer.Write((uint)images[i].Length); writer.Write((uint)offset);
                offset += images[i].Length;
            }
            foreach (byte[] image in images) writer.Write(image);
        }
    }
}
'@
}
[MirrorlyIconAssets]::Build($source, $assets)
Write-Output "Built Mirrorly icon assets from the approved 1254px PNG: $assets"
