# Portable redistribution materials

This is the tracked Gate 6A / 6A.1 material bundle. `payload/` is copied into
the portable root by `Build-Portable.ps1`; no legal text is fetched during a
release build. `manifest.json` is build-side provenance and validation data,
not another runtime dependency or a license-management service.

Vendor files are byte-preserved. `manifest.json` records their exact source
package/version or URL, SHA-256, archive member where applicable, and output
hash. Python incorporated notices retain only the selected v3.13.15 document
sections. HACL notices retain original leading license comments. BLAKE3 notices
retain full license blocks for the approved Windows runtime closure, deduplicated
by identical bytes. Authored headings are separate from vendor text.

The .NET Library License HTML and Windows SDK RTF preserve their original
encoding and formatting. Do not normalize, reformat, or "correct" vendor files.
The shared .NET MIT text also covers Tensors; Microsoft MIT covers the exact
C#/WinRT and HTML marker versions. The Windows App SDK Runtime aggregate NOTICE
also contains the Windows ML notices. Existing Python and wheel licenses stay
in their original payload locations. Do not add duplicate copies for symmetry.

To update dependencies, first establish the new primary-source mapping and
runtime closure. Then review the texts, provenance/output hashes, component
mapping and payload evidence together. Never update hashes solely to silence
a failed check. The baseline binary fingerprints tie this approved bundle to
the exact distributed components, including Rust embedded in BLAKE3; they do
not make a general security/signature claim. Launcher behavior is not pinned
here; its Release toolchain selection is checked separately.

Validation uses `Test-Redistribution.ps1 -PayloadDirectory <absolute staging>`
and `Test-Redistribution.Tests.ps1 -PayloadDirectory <absolute staging>`.
`Test-Portable.ps1` invokes the former after its normal payload checks.
Checksums are generated before validation so missing notice coverage fails.

The public download documentation must also expose the third-party terms before
download/use (Gate 8). This bundle and its tests do not by themselves declare
Gate 6 PASS: a separate read-only Gate 6C artifact audit remains required.
