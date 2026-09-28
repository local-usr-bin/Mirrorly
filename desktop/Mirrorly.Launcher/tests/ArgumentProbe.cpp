// Test-only child: report actual CRT argv/cwd, then wait for the test to release it.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cwchar>
#include <cstdint>

int wmain(int count, wchar_t** arguments)
{
    wchar_t report[32768]{}, eventName[256]{}, cwd[32768]{};
    if (!GetEnvironmentVariableW(L"MIRRORLY_LAUNCHER_TEST_REPORT", report, 32768) ||
        !GetEnvironmentVariableW(L"MIRRORLY_LAUNCHER_TEST_EVENT", eventName, 256) ||
        !GetCurrentDirectoryW(32768, cwd)) return 2;
    FILE* file = nullptr;
    if (_wfopen_s(&file, report, L"wb") != 0) return 3;
    const auto write = [file](const wchar_t* value) {
        const auto length = static_cast<uint32_t>(wcslen(value));
        fwrite(&length, sizeof(length), 1, file);
        fwrite(value, sizeof(wchar_t), length, file);
    };
    const DWORD pid = GetCurrentProcessId();
    fwrite(&pid, sizeof(pid), 1, file);
    write(cwd);
    for (int index = 0; index < count; ++index) write(arguments[index]);
    fclose(file);
    const HANDLE released = OpenEventW(SYNCHRONIZE, FALSE, eventName);
    if (!released) return 4;
    const DWORD result = WaitForSingleObject(released, 30000);
    CloseHandle(released);
    return result == WAIT_OBJECT_0 ? 0 : 5;
}
