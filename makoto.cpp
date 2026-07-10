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
    char* hdr = nl + 1;

    static const char ok[] = "MAKOTO/2.0 200 OK\r\n";
    int oklen = (int)sizeof(ok) - 1;

    char* str = strstr(hdr, "String: ");
    if (str) {
        char* val = str + 8;
        char* eol = (char*)memchr(val, '\n', inlen - (int)(val - in));
        int vlen = eol ? (int)(eol - val) : (int)(in + inlen - val);
        if (vlen > 0 && val[vlen - 1] == '\r') vlen--;

        int keep = 0;
        for (int i = 0; i < vlen; i++)
            if (val[i] != 1) keep++;

        int before = (int)(val - in);
        int after = before + vlen;

        int outlen = oklen + (before - (int)(hdr - in)) + keep + (inlen - after);
        HGLOBAL h2 = GlobalAlloc(GMEM_FIXED, outlen + 1);
        if (h2) {
            char* p = (char*)h2;
            memcpy(p, ok, oklen); p += oklen;
            memcpy(p, hdr, before - (int)(hdr - in)); p += before - (int)(hdr - in);
            for (int i = 0; i < vlen; i++)
                if (val[i] != 1) *p++ = val[i];
            memcpy(p, in + after, inlen - after);
            p[inlen - after] = 0;
            *len = outlen;
        }
        GlobalFree(h);
        return h2;
    }

    int rest = (int)(in + inlen - hdr);
    HGLOBAL h2 = GlobalAlloc(GMEM_FIXED, oklen + rest + 1);
    if (h2) {
        char* p = (char*)h2;
        memcpy(p, ok, oklen);
        memcpy(p + oklen, hdr, rest);
        p[oklen + rest] = 0;
        *len = oklen + rest;
    }
    GlobalFree(h);
    return h2;
}
