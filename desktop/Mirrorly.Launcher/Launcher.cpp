#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <string>

namespace {
int Fail(const wchar_t* reason, DWORD code)
{
    const auto message = std::wstring(reason) + L"\n\nWindows error: " + std::to_wstring(code);
    MessageBoxW(nullptr, message.c_str(), L"Mirrorly", MB_OK | MB_ICONERROR);
    return 1;
}

int Launch(const wchar_t* arguments, int show)
{
    // Grow rather than silently truncating a relocated executable's path.
    std::wstring ownPath(256, L'\0');
    for (;;) {
        const DWORD length = GetModuleFileNameW(nullptr, ownPath.data(), static_cast<DWORD>(ownPath.size()));
        if (length == 0) return Fail(L"Mirrorly could not locate its program folder.", GetLastError());
        if (length < ownPath.size()) { ownPath.resize(length); break; }
        if (ownPath.size() >= 32768) return Fail(L"Mirrorly's program path is too long.", ERROR_FILENAME_EXCED_RANGE);
        ownPath.resize(ownPath.size() * 2);
    }
    const auto separator = ownPath.find_last_of(L'\\');
    if (separator == std::wstring::npos) return Fail(L"Mirrorly could not locate its program folder.", ERROR_BAD_PATHNAME);
    const auto directory = ownPath.substr(0, separator) + L"\\app\\gui";
    const auto child = directory + L"\\Mirrorly.Desktop.exe";
    const DWORD attributes = GetFileAttributesW(child.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES)
        return Fail(L"Mirrorly could not find app\\gui\\Mirrorly.Desktop.exe. Extract the entire portable folder and try again.", GetLastError());
    if ((attributes & FILE_ATTRIBUTE_DIRECTORY) != 0)
        return Fail(L"Mirrorly's GUI payload is not an executable file. Extract the entire portable folder and try again.", ERROR_DIRECTORY);

    // wWinMain supplies the original command-line tail, excluding argv[0]. Do not
    // parse/re-quote it: preserve empty arguments, embedded quotes and backslashes.
    // A Windows filename cannot contain a quote, so quoting just argv[0] is safe.
    std::wstring command = L"\"" + child + L"\"";
    if (arguments && *arguments) { command += L' '; command += arguments; }
    if (command.size() >= 32767) return Fail(L"Mirrorly's command line is too long.", ERROR_BAD_LENGTH);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = static_cast<WORD>(show);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(child.c_str(), command.data(), nullptr, nullptr, FALSE, 0,
        nullptr, directory.c_str(), &startup, &process))
        return Fail(L"Mirrorly could not start its GUI. Check that the entire portable folder was extracted.", GetLastError());
    // The GUI owns its worker and lifecycle. The launcher never waits or restarts.
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR arguments, int show)
{
    try { return Launch(arguments, show); }
    catch (...) {
        MessageBoxW(nullptr, L"Mirrorly could not prepare the GUI launch.", L"Mirrorly", MB_OK | MB_ICONERROR);
        return 1;
    }
}
