#include <windows.h>
#include <cstring>

extern "C" __declspec(dllexport) BOOL __cdecl load(HGLOBAL h, long len) { GlobalFree(h); return TRUE; }
extern "C" __declspec(dllexport) BOOL __cdecl loadu(HGLOBAL h, long len) { GlobalFree(h); return TRUE; }
extern "C" __declspec(dllexport) BOOL __cdecl unload() { return TRUE; }

extern "C" __declspec(dllexport) HGLOBAL __cdecl request(HGLOBAL h, long* len)
{
    char* in = (char*)h;
    int inlen = (int)*len;

    char* nl = (char*)memchr(in, '\n', inlen);
    if (!nl) return h;

    static const char ok[] = "MAKOTO/2.0 200 OK\r\n";
    int oklen = (int)sizeof(ok) - 1;
    int rest = (int)(in + inlen - (nl + 1));

    HGLOBAL h2 = GlobalAlloc(GMEM_FIXED, oklen + rest + 1);
    if (h2) {
        char* p = (char*)h2;
        memcpy(p, ok, oklen);
        memcpy(p + oklen, nl + 1, rest);
        p[oklen + rest] = 0;
        *len = oklen + rest;
    }
    GlobalFree(h);
    return h2;
}
