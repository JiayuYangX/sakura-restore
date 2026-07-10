#include <windows.h>
#include <cstring>
#include <cstdlib>
#include "replace_table.inc"

struct Entry {
    int olen, rlen;
    const unsigned char* ori;
    const unsigned char* repl;
};

static Entry s_entries[REPL_COUNT];
static int s_init = 0;

static void ensure_entries() {
    if (s_init) return;
    for (int i = 0; i < REPL_COUNT; i++) {
        const unsigned char* m = REPL_META + i * 8;
        int olen = m[0] | (m[1] << 8);
        int rlen = m[2] | (m[3] << 8);
        int off  = m[4] | (m[5] << 8) | (m[6] << 16) | (m[7] << 24);
        s_entries[i].olen = olen;
        s_entries[i].rlen = rlen;
        s_entries[i].ori  = REPL_BLOB + off;
        s_entries[i].repl = REPL_BLOB + off + olen;
    }
    s_init = 1;
}

static int cp_from_charset(const char* p, int len) {
    struct { const char* n; int nl; int cp; } tbl[] = {
        {"UTF-8", 5, 65001}, {"utf-8", 5, 65001},
        {"Shift_JIS", 9, 932}, {"shift_jis", 9, 932},
        {"SJIS", 4, 932},
        {"GBK", 3, 936}, {"gbk", 3, 936},
        {"GB2312", 6, 936}, {"gb2312", 6, 936},
    };
    for (int i = 0; i < sizeof(tbl) / sizeof(tbl[0]); i++)
        if (len == tbl[i].nl && memcmp(p, tbl[i].n, tbl[i].nl) == 0)
            return tbl[i].cp;
    return 0;
}

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
    int hdr_off = (int)(hdr - in);

    // Find String: line
    char* str_line = strstr(hdr, "String: ");
    if (!str_line) {
        static const char ok[] = "MAKOTO/2.0 200 OK\r\n";
        int rest = inlen - hdr_off;
        HGLOBAL h2 = GlobalAlloc(GMEM_FIXED, sizeof(ok) - 1 + rest + 1);
        if (h2) {
            char* p = (char*)h2;
            memcpy(p, ok, sizeof(ok) - 1);
            memcpy(p + sizeof(ok) - 1, hdr, rest);
            p[sizeof(ok) - 1 + rest] = 0;
            *len = (int)(sizeof(ok) - 1 + rest);
        }
        GlobalFree(h);
        return h2;
    }

    char* str_val = str_line + 8;
    char* str_eol = (char*)memchr(str_val, '\n', inlen - (int)(str_val - in));
    int str_vlen = str_eol ? (int)(str_eol - str_val) : (int)(in + inlen - str_val);
    if (str_vlen > 0 && str_val[str_vlen - 1] == '\r') str_vlen--;

    int str_line_start = (int)(str_line - in);
    int str_line_end   = str_eol ? (int)(str_eol + 1 - in) : inlen;

    // Find Charset: header
    int codepage = 65001;
    int cs_start = -1, cs_end = -1;
    char* cs_line = strstr(hdr, "Charset: ");
    if (cs_line && cs_line < str_line) {
        char* cs_val = cs_line + 9;
        char* cs_eol = (char*)memchr(cs_val, '\n', inlen - (int)(cs_val - in));
        int cs_vlen = cs_eol ? (int)(cs_eol - cs_val) : (int)(in + inlen - cs_val);
        if (cs_vlen > 0 && cs_val[cs_vlen - 1] == '\r') cs_vlen--;
        cs_start = (int)(cs_line - in);
        cs_end   = cs_eol ? (int)(cs_eol + 1 - in) : inlen;

        int cp = cp_from_charset(cs_val, cs_vlen);
        if (cp) codepage = cp;
    }

    // ---- Convert String value to GBK ----
    char* gbk = NULL;
    int gbk_len = 0;

    if (codepage == 936) {
        gbk_len = str_vlen;
        gbk = (char*)malloc(gbk_len + 1);
        memcpy(gbk, str_val, gbk_len);
        gbk[gbk_len] = 0;
    } else {
        int wlen = MultiByteToWideChar(codepage, 0, str_val, str_vlen, NULL, 0);
        if (wlen > 0) {
            wchar_t* ws = (wchar_t*)malloc(wlen * sizeof(wchar_t));
            MultiByteToWideChar(codepage, 0, str_val, str_vlen, ws, wlen);
            gbk_len = WideCharToMultiByte(936, 0, ws, wlen, NULL, 0, NULL, NULL);
            if (gbk_len > 0) {
                gbk = (char*)malloc(gbk_len + 1);
                WideCharToMultiByte(936, 0, ws, wlen, gbk, gbk_len, NULL, NULL);
                gbk[gbk_len] = 0;
            }
            free(ws);
        }
    }

    if (!gbk) {
        gbk_len = str_vlen;
        gbk = (char*)malloc(gbk_len + 1);
        memcpy(gbk, str_val, gbk_len);
        gbk[gbk_len] = 0;
    }

    // ---- Apply SJIS→GBK replacements ----
    ensure_entries();

    int cap = gbk_len * 2 + 1;
    char* rep = (char*)malloc(cap);
    int rpos = 0;

    for (int i = 0; i < gbk_len; ) {
        int hit = 0;
        for (int e = 0; e < REPL_COUNT; e++) {
            if (i + s_entries[e].olen > gbk_len) continue;
            if (memcmp(gbk + i, s_entries[e].ori, s_entries[e].olen) == 0) {
                int nr = rpos + s_entries[e].rlen;
                if (nr > cap) {
                    cap = cap * 2 + s_entries[e].rlen;
                    rep = (char*)realloc(rep, cap);
                }
                memcpy(rep + rpos, s_entries[e].repl, s_entries[e].rlen);
                rpos = nr;
                i += s_entries[e].olen;
                hit = 1;
                break;
            }
        }
        if (!hit) {
            if ((unsigned char)gbk[i] == 0x01) { i++; continue; }
            if (rpos + 1 > cap) {
                cap = cap * 2 + 1;
                rep = (char*)realloc(rep, cap);
            }
            rep[rpos++] = gbk[i++];
        }
    }

    free(gbk);
    int rep_len = rpos;

    // ---- Build output ----
    static const char ok[]   = "MAKOTO/2.0 200 OK\r\n";
    static const char cs_gbk[] = "Charset: GBK\r\n";
    static const char str_hdr[] = "String: ";

    int est = sizeof(ok) - 1 + inlen + sizeof(cs_gbk) - 1 + rep_len + 2 + 1;
    char* out = (char*)malloc(est);
    int opos = 0;

    memcpy(out + opos, ok, sizeof(ok) - 1);
    opos += sizeof(ok) - 1;

    if (cs_start >= 0) {
        int before = cs_start - hdr_off;
        memcpy(out + opos, hdr, before);
        opos += before;

        memcpy(out + opos, cs_gbk, sizeof(cs_gbk) - 1);
        opos += sizeof(cs_gbk) - 1;

        int between = str_line_start - cs_end;
        if (between > 0) {
            memcpy(out + opos, in + cs_end, between);
            opos += between;
        }
    } else {
        int before = str_line_start - hdr_off;
        memcpy(out + opos, hdr, before);
        opos += before;
        memcpy(out + opos, cs_gbk, sizeof(cs_gbk) - 1);
        opos += sizeof(cs_gbk) - 1;
    }

    memcpy(out + opos, str_hdr, sizeof(str_hdr) - 1);
    opos += sizeof(str_hdr) - 1;
    memcpy(out + opos, rep, rep_len);
    opos += rep_len;
    out[opos++] = '\r';
    out[opos++] = '\n';

    int after = inlen - str_line_end;
    if (after > 0) {
        memcpy(out + opos, in + str_line_end, after);
        opos += after;
    }

    HGLOBAL h2 = GlobalAlloc(GMEM_FIXED, opos + 1);
    if (h2) {
        memcpy((char*)h2, out, opos);
        ((char*)h2)[opos] = 0;
        *len = opos;
    }

    free(out);
    free(rep);
    GlobalFree(h);
    return h2;
}
