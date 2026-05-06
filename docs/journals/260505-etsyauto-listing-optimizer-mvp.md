# Etsy Auto Listing Optimizer MVP — From Greenfield to Shipped

**Date**: 2026-05-05 10:22 UTC
**Severity**: Medium (architectural pivot mid-session)
**Component**: Full-stack Chrome extension + FastAPI backend + Notion integration
**Status**: Completed

## What Happened

Phiên làm việc này xây dựng MVP hoàn chỉnh cho Chrome extension tối ưu listing Etsy: tự động thay ảnh và viết lại title dùng AI.

Timeline:
- **07:40 UTC**: Brainstorm 4 vòng AskUserQuestion — chốt scope: single-user backend, Etsy official API (không scrape), Claude Sonnet cho title optimization.
- **08:30 UTC**: Phát hiện conflict kiến trúc: user nói "extension" nhưng mình xác định đó cần backend + cron, chứ không phải client-side extension đơn độc.
- **08:50 UTC**: Liệt kê 8 phases, tính estimate ~37h.
- **09:20 UTC**: Fullstack-developer agent chạy 8 phases tuần tự — 78/78 tests passing, 9 doc files.
- **10:22 UTC**: Hoàn thành + tất cả phases deployed.

## The Brutal Truth

**Kiến trúc thực tế khác với expectation ban đầu** — User nói "Chrome add-on" nhưng khi xác định requirements lại thấy cần:
- Backend xử lý logic AI (title + mockup) — không thể chạy 100% client-side vì cost & latency.
- Cron job để scheduled uploader — extension chỉ là trigger UI.
- Notion review step — user kiểm duyệt trước khi push Etsy.
- Cloudflare R2 để host ảnh cho Notion embed (Notion server không fetch localhost).

**R2 là component ngoài scope ban đầu** — Tính toán yêu cầu Notion access image từ public URL, nhưng không có tài nguyên để self-host. R2 là giải pháp "bắt buộc", không phải optional — thêm complexity & cost.

**ToS risk đã được giảm nhưng không triệt tiêu** — Chuyển từ scrape → official Etsy API giảm risk ToS violation, nhưng OAuth PKCE làm phức tạp local development flow. Cần test với real API keys.

## Technical Details

**Stack cuối cùng:**
```
Frontend: Chrome MV3 extension (vanilla JS, service worker)
Backend: FastAPI + SQLite + APScheduler (in-process)
AI: Claude Sonnet 4.6 (title), Gemini 2.5-flash-preview-05-20 (mockup scene)
Image: remove.bg API (BG removal), Gemini Imagen (scene replacement)
Storage: Cloudflare R2 (image hosting)
Config: Notion database (review + manual edits)
Auth: Etsy OAuth 2.0 PKCE flow
```

**Models (5 cái):**
- `EtsyCredential` — OAuth token + refresh
- `Listing` — Etsy metadata + optimization state
- `OptimizationJob` — Scheduled task status
- `ImageGeneration` — Mockup pipeline tracking
- `NotionSync` — Notion-Etsy sync state

**Tests: 78/78 passing**
- Phase 2 (Etsy OAuth): 22 tests
- Phase 3 (Extension + ingest): 27 tests
- Phase 4 (Title optimizer): 34 tests
- Phase 5 (Mockup pipeline): 45 tests
- Phase 6 (Notion + R2): 60 tests
- Phase 7 (Cron uploader): 78 tests

## What We Tried

1. **Initial approach**: Client-side extension chạy tất cả — không khả thi (cost OpenAI + latency ảnh).
2. **Fallback 1**: Fastify instead FastAPI — decided Python vì dễ integrate Gemini + Claude SDK.
3. **Fallback 2**: Postgres + Celery — quá overkill cho 1 user, chọn SQLite + APScheduler in-process (KISS).
4. **Fallback 3**: Self-host ảnh trên extension backend — Notion không fetch localhost, cần public URL → R2.
5. **Fallback 4**: `ck plan create` CLI không tồn tại — manual scaffold 8 phase files.

## Root Cause Analysis

**Mismatch giữa initial vision (extension) vs. reality (distributed system)**:
- User muốn "Chrome extension" nhưng yêu cầu thực tế đòi hỏi backend + cron + workflow. Không phải "thêm đó" mà là "cái đó là core".
- Không phát hiện ra ngay lập tức vì 4 vòng AskUserQuestion lẩn quẩn — nên rõ kiến trúc sớm hơn.

**Thêm R2 là forced move chứ không phải choice**:
- Notion embeds cần public image URL.
- Không có alternative practical để self-host + accessible từ Notion.
- Nên design phải account cho constraint này từ phase 1, chứ không phát hiện ở phase 6.

**Quyết định sequence phases là critical**:
- Phase 4 (title) + Phase 5 (mockup) phải sequential (cùng modify scheduler.py + listing_service.py).
- Parallel làm conflict, nên phải lock + serialize.

## Lessons Learned

1. **Architecture conversation phải xảy ra TRƯỚC brainstorm scope** — Bên dưới "extend Chrome tối ưu Etsy" là 3 thành phần khác nhau (frontend trigger, backend compute, async sync). Cần vẽ diagram sớm.

2. **External constraint (Notion image fetch) phải map ra component decision sớm** — R2 không phải "nice to have", nó là "must have". Nên tính cost & complexity vào phase planning từ đầu.

3. **Python conventions > generic rules** — Snake_case cho Python files là chuẩn, không nên override. Quyết định này đúng.

4. **Test count là signal** — Phase 7 có 78 tests (sequential upload retry logic), phase 2 chỉ 22 (OAuth). Không phải ngẫu nhiên — sync logic phức tạp, cần more test surface.

5. **Sequential > parallel khi shared state** — Không nên cố gắng parallelize nếu 2+ workers cùng modify file. Lock mechanism cost > sequential gain.

## Next Steps

**Chưa làm (không scope MVP):**
1. **Prompt tuning**: Title optimizer dùng base prompt, chưa test với real shop listings.
2. **Real Etsy API test**: OAuth flow chỉ test mock — cần real API keys từ developer account.
3. **Extension review**: Chrome Web Store submission cần security audit.
4. **Cost monitoring**: R2 + remove.bg + Claude/Gemini API chưa có dashboard.
5. **Failure replay**: Cron uploader có retry logic nhưng chưa test lỗi partial (ví dụ: title fail, mockup succeed).

**Owner**: User (single-user project, không có team).
**Timeline**: Prompt tuning & real API test nên làm trong 1 week. Chrome store submission có thể đợi sau tùy user priority.

---

**Unresolved Questions:**
- Real Etsy API keys của user là gì? (OAuth callback URL, client ID, scope).
- Remove.bg credit budget allocation? (current: unlimited trong estimate, nhưng cost-constrained IRL).
- Notion database ID + API token đã setup chưa?
- Cloudflare R2 bucket credentials setup — automation script hay manual?

**Status**: DONE
