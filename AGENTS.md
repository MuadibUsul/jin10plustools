# Project requirements

- Keep the original `plus.tlines.tech` service running while validating updates on the independent `/test/` service. Do not replace production with the test database snapshot.
- For 固定快讯, Plus新鲜事 and Plus注解辅助, all final image downloads (including future AI-generated images) must use `jin10-server/app/services/image_delivery.py`.
- Use the unmodified original `app/assets/plus-watermark.png` at the lower right, scaled proportionally. Final files must be at most 266,000 bytes. Optimize compression before reducing dimensions; preserve readable text and chart lines. Do not bypass a clarity/size failure with an oversized or illegible download.
- Download filenames are `YYYYMMDD（MD5(title UTF-8)）` plus the actual image extension. The date is the issue's production date. Keep internal original data and image snapshots for reproducible rendering.
- All fixed explanatory notes must remain editable and versioned. Category defaults are team-scoped. Only valuation notes move from body to image for now; retain existing placement for other categories until requested otherwise.
- The market valuation composite chart was approved on 2026-10-15 and is frozen. Preserve its equal-sized aligned panels, matching typography and axes styling, Buffett 0.5–1.3 reference scale with 1.0/0.7/0.5 level labels, editable image notes, original watermark and 266,000-byte delivery limit. Change this chart only on a new explicit user request.
- Plus新鲜事 and Plus注解辅助 are currently placeholders. These rules govern their future implementation; do not claim their generation workflows already exist.
