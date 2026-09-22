# Inventory: clodex wirescope captures 2026-08-20..2026-09-06

Total request captures in window: 65021 (requests lacking a .response.json: 455). role/model read from `.response.json` header; ts/agent from `.request.json` header; session = directory name.

## Q1a. Agent FAMILIES (per-task prefixes collapsed): request count, sessions, models, roles

| family | distinct prefixes | requests | session dirs | models | roles |
|---|---|---|---|---|---|
| `clodex-clodex.t*.hand` | 264 | 25446 | 286 | opus-5:22758, sonnet-5:2520, fable-5-1:108, ?:57, haiku-4-5-20251001:3 | parent:23280, subagent:1159, general-purpose:619, unknown:331, (no-response-file):57 |
| `clodex-clodex` | 1 | 16585 | 13 | opus-5:13867, fable-5-1:2060, sonnet-5:563, haiku-4-5-20251001:37, opus-4-8:33, ?:25 | parent:15640, subagent:644, general-purpose:140, unknown:136, (no-response-file):25 |
| `clodex-clodex.t*.review-rN` | 445 | 8881 | 462 | opus-5:8439, fable-5-1:426, ?:16 | parent:8403, unknown:462, (no-response-file):16 |
| `clodex-trader` | 1 | 3954 | 27 | opus-5:3788, sonnet-5:94, haiku-4-5-20251001:50, ?:22 | parent:3073, subagent:400, general-purpose:311, unknown:148, (no-response-file):22 |
| `clodex-stocks` | 1 | 1765 | 3 | opus-5:956, sonnet-5:787, fable-5-1:21, ?:1 | subagent:1106, parent:532, unknown:126, (no-response-file):1 |
| `crypto-<token>` | 18 | 1373 | 27 | sonnet-5:1371, ?:2 | subagent:1138, unknown:228, general-purpose:5, (no-response-file):2 |
| `clodex-wirescope` | 1 | 1049 | 6 | opus-5:630, fable-5-1:399, sonnet-5:12, fable-5.1:5, ?:2, fable-5:1 | parent:902, unknown:128, subagent:17, (no-response-file):2 |
| `clodex-fable-audit-tests` | 1 | 922 | 1 | fable-5:911, ?:11 | subagent:599, general-purpose:255, parent:57, (no-response-file):11 |
| `clodex-team` | 1 | 898 | 4 | opus-5:790, sonnet-5:107, ?:1 | parent:501, subagent:211, unknown:128, general-purpose:57, (no-response-file):1 |
| `clodex-Fablex` | 1 | 819 | 16 | sonnet-5:406, fable-5-1:364, haiku-4-5-20251001:36, opus-5:13 | parent:342, general-purpose:319, subagent:102, unknown:56 |
| `clodex-claude-code` | 1 | 389 | 32 | opus-5:370, fable-5-1:18, ?:1 | parent:248, unknown:140, (no-response-file):1 |
| `clodex-Plugins` | 1 | 343 | 1 | opus-5:343 | parent:328, unknown:15 |
| `crypto-daily-<date>` | 18 | 231 | 18 | sonnet-5:220, ?:11 | unknown:220, (no-response-file):11 |
| `crypto-macro-<date>` | 18 | 213 | 18 | sonnet-5:213 | unknown:213 |
| `clodex-fable-audit-renderer` | 1 | 161 | 1 | fable-5:129, opus-5:29, ?:3 | parent:102, general-purpose:56, (no-response-file):3 |
| `tl` | 1 | 136 | 1 | opus-4-8:112, ?:24 | parent:110, (no-response-file):24, subagent:2 |
| `clodex-contrarian` | 1 | 125 | 11 | ?:125 | (no-response-file):125 |
| `opus` | 1 | 125 | 1 | opus-4-8:99, ?:26 | parent:78, (no-response-file):26, subagent:21 |
| `clodex-clodex-designer-<n>` | 4 | 117 | 4 | fable-5-1:64, fable-5:53 | parent:112, unknown:5 |
| `clodex-fable-audit-session` | 1 | 105 | 1 | fable-5:99, opus-4-8:4, ?:2 | parent:56, subagent:46, (no-response-file):2, unknown:1 |
| `clodex-clodex-hand-<hex>` | 2 | 83 | 2 | opus-5:82, ?:1 | parent:80, unknown:2, (no-response-file):1 |
| `clodex-clodex.review-r1` | 1 | 70 | 3 | opus-5:70 | parent:67, unknown:3 |
| `codex` | 1 | 67 | 2 | ?:67 | (no-response-file):67 |
| `clodex-fable-audit-composition` | 1 | 56 | 1 | fable-5:30, opus-5:23, ?:2, opus-4-8:1 | parent:53, (no-response-file):2, unknown:1 |
| `ext` | 1 | 56 | 20 | ?:56 | (no-response-file):56 |
| `clodex` | 1 | 55 | 17 | haiku-4-5-20251001:49, fable-5-1:4, opus-5:2 | parent:38, unknown:17 |
| `clodex-fable-audit-data` | 1 | 54 | 1 | fable-5:52, opus-5:2 | parent:52, unknown:2 |
| `clodex-fable-audit-team` | 1 | 40 | 1 | fable-5:40 | parent:39, unknown:1 |
| `brief-fluid` | 1 | 28 | 14 | haiku-4-5-20251001:28 | unknown:14, parent:14 |
| `brief-cow` | 1 | 26 | 13 | haiku-4-5-20251001:26 | unknown:13, parent:13 |
| `brief-ena` | 1 | 26 | 13 | haiku-4-5-20251001:26 | unknown:13, parent:13 |
| `brief-par` | 1 | 24 | 12 | haiku-4-5-20251001:24 | unknown:12, parent:12 |
| `brief-aave` | 1 | 24 | 12 | haiku-4-5-20251001:24 | unknown:12, parent:12 |
| `brief-pro` | 1 | 24 | 12 | haiku-4-5-20251001:24 | unknown:12, parent:12 |
| `brief-resolv` | 1 | 23 | 11 | haiku-4-5-20251001:23 | parent:12, unknown:11 |
| `brief-spk` | 1 | 23 | 11 | haiku-4-5-20251001:23 | parent:12, unknown:11 |
| `brief-baby` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-cfg` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-drv` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-eul` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-gfi` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-gmx` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-kmno` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-ldo` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-morpho` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-ssv` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-syrup` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-tru` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-uni` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-well` | 1 | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-jup` | 1 | 18 | 9 | haiku-4-5-20251001:18 | unknown:9, parent:9 |
| `brief-ondo` | 1 | 18 | 9 | haiku-4-5-20251001:18 | unknown:9, parent:9 |
| `brief-huma` | 1 | 18 | 9 | haiku-4-5-20251001:18 | unknown:9, parent:9 |
| `clodex-clodex-designer-audit` | 1 | 17 | 1 | fable-5:17 | parent:16, unknown:1 |
| `brief-chex` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-cpool` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-drift` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-giza` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-hype` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-link` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-mamo` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-pendle` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-sei` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-snx` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-stream` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-synai` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-trade` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-velo` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-zbcn` | 1 | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `clodex-fable-audit-network` | 1 | 14 | 1 | opus-5:9, fable-5:4, opus-4-8:1 | parent:10, unknown:2, subagent:2 |
| `auto-<n>-w<n>` | 1 | 13 | 1 | sonnet-4-6:13 | parent:12, unknown:1 |
| `clodex-Analyst` | 1 | 12 | 12 | fable-5:12 | unknown:12 |
| `ab-compact-sonnet` | 1 | 8 | 8 | sonnet-5:8 | parent:8 |
| `oracle` | 1 | 7 | 1 | sonnet-5:7 | parent:7 |
| `clodex-pluginx` | 1 | 7 | 1 | opus-5:7 | parent:6, unknown:1 |
| `repo-2` | 1 | 2 | 2 | sonnet-5:2 | parent:2 |
| `repo-3` | 1 | 2 | 2 | sonnet-5:2 | parent:2 |
| `repo-1` | 1 | 2 | 2 | sonnet-5:2 | parent:2 |
| `ws-teapot-base` | 1 | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-strict` | 1 | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-relaxed` | 1 | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-off0` | 1 | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-offfalse` | 1 | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-o-base` | 1 | 2 | 1 | opus-5:2 | unknown:2 |
| `ws-teapot-o-off0` | 1 | 2 | 1 | opus-5:2 | unknown:2 |
| `ws-teapot-o-relaxed` | 1 | 2 | 1 | opus-5:2 | unknown:2 |
| `executor-1` | 1 | 1 | 1 | sonnet-4-6:1 | unknown:1 |
| `executor-2` | 1 | 1 | 1 | sonnet-4-6:1 | unknown:1 |
| `ctx` | 1 | 1 | 1 | fable-5-1:1 | unknown:1 |

## Q1b. Every distinct agent prefix (full list)

| prefix | requests | session dirs | models | roles |
|---|---|---|---|---|
| `clodex-clodex` | 16585 | 13 | opus-5:13867, fable-5-1:2060, sonnet-5:563, haiku-4-5-20251001:37, opus-4-8:33, ?:25 | parent:15640, subagent:644, general-purpose:140, unknown:136, (no-response-file):25 |
| `clodex-trader` | 3954 | 27 | opus-5:3788, sonnet-5:94, haiku-4-5-20251001:50, ?:22 | parent:3073, subagent:400, general-purpose:311, unknown:148, (no-response-file):22 |
| `clodex-stocks` | 1765 | 3 | opus-5:956, sonnet-5:787, fable-5-1:21, ?:1 | subagent:1106, parent:532, unknown:126, (no-response-file):1 |
| `clodex-wirescope` | 1049 | 6 | opus-5:630, fable-5-1:399, sonnet-5:12, fable-5.1:5, ?:2, fable-5:1 | parent:902, unknown:128, subagent:17, (no-response-file):2 |
| `clodex-clodex.t559.hand` | 1018 | 1 | sonnet-5:773, opus-5:243, ?:2 | subagent:773, parent:242, (no-response-file):2, unknown:1 |
| `clodex-fable-audit-tests` | 922 | 1 | fable-5:911, ?:11 | subagent:599, general-purpose:255, parent:57, (no-response-file):11 |
| `clodex-team` | 898 | 4 | opus-5:790, sonnet-5:107, ?:1 | parent:501, subagent:211, unknown:128, general-purpose:57, (no-response-file):1 |
| `clodex-Fablex` | 819 | 16 | sonnet-5:406, fable-5-1:364, haiku-4-5-20251001:36, opus-5:13 | parent:342, general-purpose:319, subagent:102, unknown:56 |
| `clodex-clodex.t683.hand` | 600 | 1 | sonnet-5:600 | parent:599, unknown:1 |
| `clodex-clodex.t560.hand` | 495 | 1 | sonnet-5:371, opus-5:124 | general-purpose:371, parent:123, unknown:1 |
| `clodex-clodex.t673.hand` | 490 | 13 | opus-5:452, fable-5-1:38 | parent:451, unknown:39 |
| `clodex-clodex.t685.hand` | 432 | 2 | opus-5:429, fable-5-1:3 | parent:360, general-purpose:67, unknown:5 |
| `clodex-clodex.t654.hand` | 405 | 1 | opus-5:405 | parent:404, unknown:1 |
| `clodex-clodex.t699.hand` | 394 | 1 | opus-5:359, ?:35 | parent:335, (no-response-file):35, subagent:23, unknown:1 |
| `clodex-claude-code` | 389 | 32 | opus-5:370, fable-5-1:18, ?:1 | parent:248, unknown:140, (no-response-file):1 |
| `clodex-clodex.t681.hand` | 386 | 1 | sonnet-5:386 | parent:385, unknown:1 |
| `clodex-Plugins` | 343 | 1 | opus-5:343 | parent:328, unknown:15 |
| `clodex-clodex.t645.hand` | 328 | 4 | opus-5:321, fable-5-1:7 | parent:320, unknown:8 |
| `clodex-clodex.t465.hand` | 324 | 2 | opus-5:324 | parent:322, unknown:2 |
| `crypto-moonwell` | 294 | 4 | sonnet-5:294 | subagent:235, unknown:59 |
| `clodex-clodex.t679.hand` | 294 | 1 | opus-5:294 | parent:293, unknown:1 |
| `clodex-clodex.t650.hand` | 291 | 1 | opus-5:291 | parent:290, unknown:1 |
| `clodex-clodex.t661.hand` | 274 | 1 | opus-5:274 | parent:273, unknown:1 |
| `clodex-clodex.t571.hand` | 269 | 1 | opus-5:269 | parent:268, unknown:1 |
| `clodex-clodex.t345.hand` | 235 | 1 | opus-5:235 | parent:234, unknown:1 |
| `clodex-clodex.t597.hand` | 233 | 1 | opus-5:233 | parent:232, unknown:1 |
| `clodex-clodex.t606.hand` | 218 | 1 | opus-5:218 | parent:217, unknown:1 |
| `clodex-clodex.t619.hand` | 211 | 1 | opus-5:211 | parent:210, unknown:1 |
| `clodex-clodex.t472.hand` | 210 | 1 | opus-5:210 | parent:209, unknown:1 |
| `clodex-clodex.t675.hand` | 208 | 1 | opus-5:208 | parent:207, unknown:1 |
| `clodex-clodex.t479.hand` | 204 | 1 | opus-5:204 | parent:203, unknown:1 |
| `clodex-clodex.t378.hand` | 198 | 1 | opus-5:197, ?:1 | parent:196, unknown:1, (no-response-file):1 |
| `clodex-clodex.t672.hand` | 192 | 1 | opus-5:192 | parent:191, unknown:1 |
| `clodex-clodex.t676.hand` | 192 | 1 | opus-5:192 | parent:191, unknown:1 |
| `crypto-kamino` | 188 | 4 | sonnet-5:188 | subagent:156, unknown:30, general-purpose:2 |
| `clodex-clodex.t599.hand` | 185 | 1 | opus-5:185 | parent:184, unknown:1 |
| `clodex-clodex.t600.hand` | 185 | 1 | opus-5:185 | parent:184, unknown:1 |
| `clodex-clodex.t649.hand` | 178 | 1 | opus-5:178 | parent:177, unknown:1 |
| `clodex-clodex.t701.hand` | 178 | 1 | opus-5:178 | parent:177, unknown:1 |
| `clodex-clodex.t694.hand` | 177 | 1 | opus-5:177 | parent:118, subagent:58, unknown:1 |
| `clodex-clodex.t657.hand` | 174 | 1 | opus-5:174 | parent:173, unknown:1 |
| `clodex-clodex.t674.hand` | 172 | 1 | opus-5:172 | parent:171, unknown:1 |
| `clodex-fable-audit-renderer` | 161 | 1 | fable-5:129, opus-5:29, ?:3 | parent:102, general-purpose:56, (no-response-file):3 |
| `clodex-clodex.t587.hand` | 160 | 1 | opus-5:160 | parent:159, unknown:1 |
| `clodex-clodex.t689.hand` | 158 | 1 | opus-5:158 | parent:119, general-purpose:38, unknown:1 |
| `clodex-clodex.t623.hand` | 157 | 1 | sonnet-5:93, opus-5:64 | subagent:93, parent:63, unknown:1 |
| `clodex-clodex.t511.hand` | 155 | 1 | opus-5:155 | parent:152, unknown:3 |
| `clodex-clodex.t598.hand` | 155 | 1 | opus-5:155 | parent:154, unknown:1 |
| `clodex-clodex.t572.hand` | 154 | 1 | opus-5:154 | parent:153, unknown:1 |
| `clodex-clodex.t518.hand` | 152 | 1 | opus-5:152 | parent:151, unknown:1 |
| `clodex-clodex.t697.hand` | 150 | 1 | opus-5:127, sonnet-5:23 | parent:126, general-purpose:23, unknown:1 |
| `clodex-clodex.t443.hand` | 149 | 1 | opus-5:149 | parent:148, unknown:1 |
| `clodex-clodex.t663.hand` | 145 | 1 | opus-5:145 | parent:144, unknown:1 |
| `clodex-clodex.t655.hand` | 144 | 1 | opus-5:144 | parent:143, unknown:1 |
| `clodex-clodex.t536.hand` | 143 | 1 | opus-5:143 | parent:142, unknown:1 |
| `clodex-clodex.t684.hand` | 143 | 1 | opus-5:97, sonnet-5:46 | parent:96, general-purpose:46, unknown:1 |
| `clodex-clodex.t453.hand` | 140 | 1 | opus-5:140 | parent:139, unknown:1 |
| `clodex-clodex.t489.hand` | 140 | 1 | opus-5:135, ?:5 | parent:131, (no-response-file):5, unknown:4 |
| `clodex-clodex.t498.hand` | 138 | 1 | opus-5:138 | parent:137, unknown:1 |
| `tl` | 136 | 1 | opus-4-8:112, ?:24 | parent:110, (no-response-file):24, subagent:2 |
| `clodex-clodex.t509.hand` | 136 | 2 | opus-5:136 | parent:134, unknown:2 |
| `clodex-clodex.t491.hand` | 132 | 1 | opus-5:132 | parent:131, unknown:1 |
| `clodex-clodex.t594.hand` | 132 | 4 | opus-5:132 | parent:128, unknown:4 |
| `clodex-clodex.t607.hand` | 130 | 1 | opus-5:130 | parent:129, unknown:1 |
| `clodex-clodex.t486.hand` | 129 | 1 | opus-5:129 | parent:128, unknown:1 |
| `clodex-contrarian` | 125 | 11 | ?:125 | (no-response-file):125 |
| `opus` | 125 | 1 | opus-4-8:99, ?:26 | parent:78, (no-response-file):26, subagent:21 |
| `clodex-clodex.t553.hand` | 122 | 1 | opus-5:120, ?:2 | parent:85, subagent:34, (no-response-file):2, unknown:1 |
| `clodex-clodex.t604.hand` | 122 | 1 | opus-5:110, sonnet-5:12 | parent:109, subagent:12, unknown:1 |
| `clodex-clodex.t634.hand` | 122 | 1 | opus-5:122 | parent:121, unknown:1 |
| `clodex-clodex.t646.hand` | 122 | 1 | opus-5:122 | parent:121, unknown:1 |
| `clodex-clodex.t482.hand` | 121 | 1 | opus-5:121 | parent:120, unknown:1 |
| `clodex-clodex.t700.hand` | 120 | 1 | opus-5:110, ?:10 | parent:109, (no-response-file):10, unknown:1 |
| `clodex-clodex.t351.hand` | 119 | 1 | opus-5:119 | parent:118, unknown:1 |
| `clodex-clodex.t603.hand` | 119 | 1 | opus-5:119 | parent:118, unknown:1 |
| `clodex-clodex.t490.hand` | 118 | 1 | opus-5:118 | parent:117, unknown:1 |
| `crypto-ethena` | 115 | 2 | sonnet-5:115 | subagent:88, unknown:27 |
| `clodex-clodex.t558.hand` | 115 | 1 | opus-5:115 | parent:114, unknown:1 |
| `clodex-clodex.t581.hand` | 115 | 1 | opus-5:115 | parent:114, unknown:1 |
| `clodex-clodex.t582.hand` | 115 | 1 | opus-5:115 | parent:114, unknown:1 |
| `clodex-clodex.t564.hand` | 114 | 1 | opus-5:114 | parent:113, unknown:1 |
| `crypto-morpho` | 113 | 2 | sonnet-5:111, ?:2 | subagent:91, unknown:20, (no-response-file):2 |
| `clodex-clodex.t532.hand` | 113 | 1 | opus-5:113 | parent:112, unknown:1 |
| `clodex-clodex.t638.hand` | 113 | 1 | opus-5:113 | parent:112, unknown:1 |
| `clodex-clodex.t450.hand` | 112 | 1 | opus-5:112 | parent:111, unknown:1 |
| `clodex-clodex.t314.hand` | 112 | 1 | opus-5:112 | parent:111, unknown:1 |
| `clodex-clodex.t688.hand` | 112 | 1 | opus-5:112 | parent:111, unknown:1 |
| `clodex-clodex.t709.hand` | 112 | 1 | opus-5:112 | parent:65, subagent:46, unknown:1 |
| `clodex-clodex.t702.hand` | 109 | 1 | opus-5:109 | parent:108, unknown:1 |
| `clodex-clodex.t549.hand` | 107 | 1 | opus-5:107 | parent:106, unknown:1 |
| `clodex-clodex.t609.hand` | 106 | 1 | sonnet-5:59, opus-5:47 | subagent:59, parent:46, unknown:1 |
| `clodex-clodex.t691.hand` | 106 | 1 | opus-5:106 | parent:105, unknown:1 |
| `clodex-fable-audit-session` | 105 | 1 | fable-5:99, opus-4-8:4, ?:2 | parent:56, subagent:46, (no-response-file):2, unknown:1 |
| `clodex-clodex.t602.hand` | 105 | 2 | opus-5:105 | parent:103, unknown:2 |
| `clodex-clodex.t585.hand` | 104 | 1 | opus-5:62, sonnet-5:42 | parent:61, general-purpose:42, unknown:1 |
| `clodex-clodex.t707.hand` | 104 | 1 | opus-5:104 | parent:103, unknown:1 |
| `clodex-clodex.t577.hand` | 103 | 1 | opus-5:103 | parent:102, unknown:1 |
| `clodex-clodex.t569.hand` | 102 | 1 | opus-5:102 | parent:101, unknown:1 |
| `clodex-clodex.t614.hand` | 102 | 1 | opus-5:102 | parent:101, unknown:1 |
| `clodex-clodex.t678.hand` | 102 | 1 | opus-5:102 | parent:101, unknown:1 |
| `clodex-clodex.t686.hand` | 102 | 1 | opus-5:102 | parent:101, unknown:1 |
| `clodex-clodex.t696.hand` | 102 | 1 | opus-5:102 | parent:80, subagent:21, unknown:1 |
| `clodex-clodex.t595.hand` | 101 | 1 | opus-5:101 | parent:100, unknown:1 |
| `clodex-clodex.t425.hand` | 100 | 1 | opus-5:100 | parent:98, unknown:2 |
| `clodex-clodex.t465.review-r1` | 98 | 4 | opus-5:98 | parent:94, unknown:4 |
| `clodex-clodex.t579.hand` | 98 | 1 | opus-5:98 | parent:97, unknown:1 |
| `clodex-clodex.t445.hand` | 97 | 1 | opus-5:97 | parent:96, unknown:1 |
| `clodex-clodex.t593.hand` | 97 | 1 | opus-5:97 | parent:96, unknown:1 |
| `clodex-clodex.t534.hand` | 95 | 1 | opus-5:95 | parent:94, unknown:1 |
| `clodex-clodex.t682.hand` | 94 | 1 | sonnet-5:94 | parent:82, general-purpose:8, subagent:3, unknown:1 |
| `crypto-zebec` | 92 | 2 | sonnet-5:92 | subagent:82, unknown:10 |
| `clodex-clodex.t616.hand` | 92 | 1 | opus-5:92 | parent:91, unknown:1 |
| `clodex-clodex.t648.hand` | 91 | 1 | opus-5:91 | parent:90, unknown:1 |
| `clodex-clodex.t698.hand` | 91 | 1 | opus-5:91 | parent:73, general-purpose:17, unknown:1 |
| `clodex-clodex.t635.hand` | 90 | 1 | opus-5:90 | parent:89, unknown:1 |
| `clodex-clodex.t641.hand` | 90 | 1 | opus-5:90 | parent:89, unknown:1 |
| `clodex-clodex.t573.hand` | 89 | 1 | opus-5:89 | parent:88, unknown:1 |
| `clodex-clodex.t637.hand` | 89 | 1 | opus-5:89 | parent:88, unknown:1 |
| `clodex-clodex.t660.hand` | 89 | 1 | opus-5:89 | parent:88, unknown:1 |
| `clodex-clodex.t355.hand` | 88 | 1 | opus-5:88 | parent:87, unknown:1 |
| `clodex-clodex.t471.hand` | 88 | 1 | opus-5:88 | parent:87, unknown:1 |
| `clodex-clodex.t692.hand` | 88 | 1 | opus-5:88 | parent:87, unknown:1 |
| `clodex-clodex.t710.hand` | 88 | 1 | opus-5:88 | parent:87, unknown:1 |
| `clodex-clodex.t456.hand` | 87 | 1 | opus-5:87 | parent:86, unknown:1 |
| `clodex-clodex.t620.hand` | 87 | 1 | opus-5:87 | parent:86, unknown:1 |
| `clodex-clodex.t550.hand` | 85 | 1 | opus-5:85 | parent:84, unknown:1 |
| `clodex-clodex.t452.hand` | 83 | 1 | opus-5:83 | parent:82, unknown:1 |
| `clodex-clodex.t610.hand` | 83 | 1 | opus-5:80, haiku-4-5-20251001:3 | parent:79, subagent:3, unknown:1 |
| `clodex-clodex.t627.hand` | 83 | 1 | opus-5:83 | parent:82, unknown:1 |
| `crypto-truefi` | 82 | 1 | sonnet-5:82 | subagent:70, unknown:11, general-purpose:1 |
| `clodex-clodex.t556.hand` | 81 | 1 | opus-5:80, ?:1 | parent:79, unknown:1, (no-response-file):1 |
| `clodex-clodex.t664.hand` | 81 | 1 | opus-5:81 | parent:80, unknown:1 |
| `clodex-clodex.t566.hand` | 80 | 1 | opus-5:80 | parent:79, unknown:1 |
| `clodex-clodex.t656.hand` | 80 | 1 | opus-5:80 | parent:79, unknown:1 |
| `clodex-clodex.t541.hand` | 79 | 1 | opus-5:79 | parent:78, unknown:1 |
| `clodex-clodex.t613.hand` | 79 | 1 | opus-5:79 | parent:78, unknown:1 |
| `clodex-clodex.t574.hand` | 78 | 1 | opus-5:78 | parent:76, unknown:2 |
| `clodex-clodex.t693.hand` | 78 | 1 | opus-5:78 | parent:77, unknown:1 |
| `clodex-clodex.t392.hand` | 77 | 1 | opus-5:77 | parent:76, unknown:1 |
| `clodex-clodex.t494.hand` | 77 | 1 | opus-5:77 | parent:76, unknown:1 |
| `clodex-clodex.t687.hand` | 77 | 1 | opus-5:77 | parent:76, unknown:1 |
| `clodex-clodex.t355.review-r1` | 75 | 2 | opus-5:75 | parent:73, unknown:2 |
| `clodex-clodex.t567.hand` | 75 | 1 | opus-5:75 | parent:73, unknown:2 |
| `clodex-clodex.t596.hand` | 75 | 1 | opus-5:75 | parent:74, unknown:1 |
| `clodex-clodex.t656.review-r1` | 75 | 1 | opus-5:75 | parent:74, unknown:1 |
| `crypto-pendle` | 74 | 1 | sonnet-5:74 | subagent:56, unknown:16, general-purpose:2 |
| `clodex-clodex.t517.hand` | 74 | 1 | opus-5:74 | parent:73, unknown:1 |
| `clodex-clodex.t561.hand` | 74 | 1 | opus-5:74 | parent:73, unknown:1 |
| `clodex-clodex.t639.hand` | 74 | 1 | opus-5:74 | parent:73, unknown:1 |
| `clodex-clodex.t538.hand` | 72 | 1 | opus-5:72 | parent:71, unknown:1 |
| `clodex-clodex.t570.hand` | 72 | 1 | opus-5:72 | parent:71, unknown:1 |
| `clodex-clodex.t713.hand` | 72 | 1 | opus-5:51, sonnet-5:21 | parent:50, subagent:21, unknown:1 |
| `clodex-clodex.t651.hand` | 71 | 1 | opus-5:71 | parent:70, unknown:1 |
| `clodex-clodex.t459.hand` | 70 | 1 | opus-5:70 | parent:69, unknown:1 |
| `clodex-clodex.review-r1` | 70 | 3 | opus-5:70 | parent:67, unknown:3 |
| `clodex-clodex.t487.hand` | 69 | 1 | opus-5:69 | parent:68, unknown:1 |
| `clodex-clodex.t704.hand` | 69 | 1 | opus-5:69 | parent:68, unknown:1 |
| `clodex-clodex.t521.hand` | 68 | 1 | opus-5:68 | parent:67, unknown:1 |
| `clodex-clodex.t677.hand` | 68 | 1 | opus-5:68 | parent:67, unknown:1 |
| `clodex-clodex.t708.hand` | 68 | 1 | opus-5:68 | parent:47, subagent:13, general-purpose:7, unknown:1 |
| `clodex-clodex.t450.review-r1` | 67 | 1 | opus-5:67 | parent:66, unknown:1 |
| `codex` | 67 | 2 | ?:67 | (no-response-file):67 |
| `clodex-clodex.t645.review-r1` | 67 | 2 | opus-5:67 | parent:65, unknown:2 |
| `clodex-clodex.t695.hand` | 67 | 1 | opus-5:67 | parent:66, unknown:1 |
| `clodex-clodex.t462.hand` | 66 | 1 | opus-5:66 | parent:65, unknown:1 |
| `clodex-clodex.t425.review-r1` | 66 | 1 | opus-5:66 | parent:65, unknown:1 |
| `clodex-clodex.t522.hand` | 66 | 1 | opus-5:66 | parent:65, unknown:1 |
| `clodex-clodex.t451.hand` | 65 | 1 | opus-5:65 | parent:64, unknown:1 |
| `clodex-clodex.t470.hand` | 64 | 1 | opus-5:64 | parent:63, unknown:1 |
| `clodex-clodex.t632.hand` | 63 | 1 | opus-5:63 | parent:62, unknown:1 |
| `clodex-clodex.t662.hand` | 62 | 1 | opus-5:62 | parent:61, unknown:1 |
| `clodex-clodex.t680.hand` | 60 | 1 | fable-5-1:60 | parent:59, unknown:1 |
| `clodex-clodex.t474.hand` | 59 | 1 | opus-5:59 | parent:58, unknown:1 |
| `clodex-clodex.t544.hand` | 59 | 1 | opus-5:59 | parent:58, unknown:1 |
| `clodex-clodex.t671.hand` | 59 | 1 | opus-5:59 | parent:58, unknown:1 |
| `clodex-clodex.t357.hand` | 58 | 1 | opus-5:58 | parent:57, unknown:1 |
| `clodex-clodex.t449.hand` | 58 | 1 | opus-5:58 | parent:57, unknown:1 |
| `clodex-clodex.t464.hand` | 58 | 1 | opus-5:58 | parent:57, unknown:1 |
| `clodex-clodex.t468.hand` | 58 | 1 | opus-5:58 | parent:57, unknown:1 |
| `clodex-clodex.t484.hand` | 58 | 1 | opus-5:58 | parent:57, unknown:1 |
| `clodex-clodex.t705.hand` | 58 | 1 | opus-5:58 | parent:57, unknown:1 |
| `clodex-clodex.t542.hand` | 57 | 1 | opus-5:57 | parent:56, unknown:1 |
| `clodex-fable-audit-composition` | 56 | 1 | fable-5:30, opus-5:23, ?:2, opus-4-8:1 | parent:53, (no-response-file):2, unknown:1 |
| `clodex-clodex.t535.hand` | 56 | 1 | opus-5:56 | parent:55, unknown:1 |
| `ext` | 56 | 20 | ?:56 | (no-response-file):56 |
| `clodex-clodex.t690.hand` | 56 | 1 | opus-5:56 | parent:55, unknown:1 |
| `clodex-clodex.t461.hand` | 55 | 1 | opus-5:55 | parent:54, unknown:1 |
| `clodex-clodex.t504.hand` | 55 | 1 | opus-5:55 | parent:54, unknown:1 |
| `clodex` | 55 | 17 | haiku-4-5-20251001:49, fable-5-1:4, opus-5:2 | parent:38, unknown:17 |
| `clodex-clodex.t624.hand` | 55 | 1 | opus-5:55 | parent:54, unknown:1 |
| `clodex-clodex.t389.hand` | 54 | 1 | opus-5:54 | parent:53, unknown:1 |
| `clodex-fable-audit-data` | 54 | 1 | fable-5:52, opus-5:2 | parent:52, unknown:2 |
| `clodex-clodex.t483.hand` | 54 | 1 | opus-5:54 | parent:53, unknown:1 |
| `clodex-clodex.t531.hand` | 54 | 1 | opus-5:54 | parent:53, unknown:1 |
| `clodex-clodex.t571.review-r4` | 54 | 1 | opus-5:54 | parent:53, unknown:1 |
| `clodex-clodex.t659.hand` | 54 | 1 | opus-5:54 | parent:53, unknown:1 |
| `clodex-clodex.t626.hand` | 53 | 1 | opus-5:53 | parent:52, unknown:1 |
| `clodex-clodex.t628.hand` | 53 | 1 | opus-5:53 | parent:52, unknown:1 |
| `clodex-clodex.t668.hand` | 53 | 1 | opus-5:53 | parent:52, unknown:1 |
| `clodex-clodex.t446.hand` | 52 | 1 | opus-5:52 | parent:51, unknown:1 |
| `clodex-clodex.t505.hand` | 52 | 1 | opus-5:52 | parent:51, unknown:1 |
| `clodex-clodex.t647.hand` | 52 | 1 | opus-5:52 | parent:51, unknown:1 |
| `clodex-clodex.t703.hand` | 52 | 1 | opus-5:52 | parent:51, unknown:1 |
| `clodex-clodex.t554.hand` | 51 | 1 | opus-5:51 | parent:50, unknown:1 |
| `clodex-clodex.t448.hand` | 50 | 1 | opus-5:50 | parent:49, unknown:1 |
| `clodex-clodex.t392.review-r1` | 50 | 1 | opus-5:50 | parent:49, unknown:1 |
| `clodex-clodex.t513.hand` | 50 | 1 | opus-5:50 | parent:49, unknown:1 |
| `clodex-clodex.t520.hand` | 50 | 1 | opus-5:50 | parent:49, unknown:1 |
| `clodex-clodex.t597.review-r2` | 50 | 1 | opus-5:50 | parent:49, unknown:1 |
| `clodex-clodex.t599.review-r1` | 50 | 2 | opus-5:50 | parent:48, unknown:2 |
| `clodex-clodex.t655.review-r1` | 50 | 1 | opus-5:50 | parent:49, unknown:1 |
| `clodex-clodex.t661.review-r1` | 50 | 1 | opus-5:50 | parent:49, unknown:1 |
| `clodex-clodex.t366.hand` | 49 | 1 | opus-5:49 | parent:48, unknown:1 |
| `clodex-clodex.t470.review-r3` | 49 | 1 | opus-5:49 | parent:48, unknown:1 |
| `clodex-clodex.t548.hand` | 49 | 1 | opus-5:49 | parent:48, unknown:1 |
| `clodex-clodex.t479.review-r3` | 49 | 1 | opus-5:49 | parent:48, unknown:1 |
| `clodex-clodex.t642.hand` | 49 | 1 | opus-5:49 | parent:48, unknown:1 |
| `clodex-clodex.t644.hand` | 49 | 1 | opus-5:49 | parent:48, unknown:1 |
| `clodex-clodex.t468.review-r1` | 48 | 1 | opus-5:48 | parent:47, unknown:1 |
| `clodex-clodex.t492.hand` | 48 | 1 | opus-5:48 | parent:47, unknown:1 |
| `clodex-clodex.t655.review-r2` | 48 | 1 | opus-5:48 | parent:47, unknown:1 |
| `clodex-clodex.t447.hand` | 47 | 1 | opus-5:47 | parent:46, unknown:1 |
| `clodex-clodex.t486.review-r2` | 47 | 3 | opus-5:47 | parent:44, unknown:3 |
| `clodex-clodex.t488.hand` | 47 | 1 | opus-5:47 | parent:46, unknown:1 |
| `clodex-clodex.t540.hand` | 47 | 1 | opus-5:47 | parent:46, unknown:1 |
| `clodex-clodex-designer-617` | 47 | 1 | fable-5-1:47 | parent:46, unknown:1 |
| `clodex-clodex.t479.review-r2` | 47 | 1 | opus-5:47 | parent:46, unknown:1 |
| `clodex-clodex.t458.hand` | 46 | 1 | opus-5:46 | parent:45, unknown:1 |
| `clodex-clodex.t496.hand` | 46 | 1 | opus-5:46 | parent:45, unknown:1 |
| `clodex-clodex.t562.hand` | 46 | 1 | opus-5:46 | parent:45, unknown:1 |
| `clodex-clodex.t630.hand` | 46 | 1 | opus-5:46 | parent:45, unknown:1 |
| `clodex-clodex.t466.hand` | 45 | 1 | opus-5:45 | parent:44, unknown:1 |
| `clodex-clodex.t392.review-r2` | 45 | 1 | opus-5:45 | parent:44, unknown:1 |
| `clodex-clodex.t470.review-r1` | 45 | 1 | opus-5:34, ?:11 | parent:33, (no-response-file):11, unknown:1 |
| `clodex-clodex.t636.hand` | 45 | 1 | opus-5:45 | parent:42, unknown:3 |
| `clodex-clodex.t670.hand` | 45 | 1 | opus-5:45 | parent:44, unknown:1 |
| `clodex-clodex.t345.review-r1` | 44 | 2 | opus-5:44 | parent:42, unknown:2 |
| `clodex-clodex.t323.hand` | 44 | 1 | opus-5:44 | parent:43, unknown:1 |
| `clodex-clodex.t525.hand` | 44 | 1 | opus-5:44 | parent:43, unknown:1 |
| `clodex-clodex.t529.hand` | 44 | 1 | opus-5:44 | parent:43, unknown:1 |
| `clodex-clodex.t551.hand` | 44 | 1 | opus-5:44 | parent:41, unknown:3 |
| `clodex-clodex.t574.review-r1` | 44 | 1 | opus-5:44 | parent:43, unknown:1 |
| `clodex-clodex.t581.review-r2` | 44 | 1 | opus-5:44 | parent:43, unknown:1 |
| `clodex-clodex.t608.hand` | 44 | 1 | opus-5:44 | parent:43, unknown:1 |
| `clodex-clodex.t667.hand` | 44 | 1 | opus-5:44 | parent:43, unknown:1 |
| `clodex-clodex.t669.hand` | 44 | 1 | opus-5:44 | parent:43, unknown:1 |
| `clodex-clodex-hand-511b` | 43 | 1 | opus-5:42, ?:1 | parent:41, unknown:1, (no-response-file):1 |
| `clodex-clodex.t578.hand` | 43 | 1 | opus-5:43 | parent:42, unknown:1 |
| `clodex-clodex.t595.review-r1` | 43 | 1 | opus-5:43 | parent:42, unknown:1 |
| `crypto-spark_protocol` | 43 | 1 | sonnet-5:43 | subagent:38, unknown:5 |
| `clodex-clodex.t460.hand` | 42 | 1 | opus-5:42 | parent:41, unknown:1 |
| `clodex-clodex.t498.review-r2` | 42 | 1 | opus-5:42 | parent:41, unknown:1 |
| `clodex-clodex.t533.hand` | 42 | 1 | opus-5:42 | parent:41, unknown:1 |
| `clodex-clodex.t558.review-r2` | 42 | 1 | opus-5:42 | parent:41, unknown:1 |
| `clodex-clodex.t597.review-r1` | 42 | 1 | opus-5:42 | parent:41, unknown:1 |
| `clodex-clodex.t453.review-r1` | 41 | 1 | opus-5:41 | parent:40, unknown:1 |
| `crypto-huma_finance` | 41 | 1 | sonnet-5:41 | subagent:36, unknown:5 |
| `crypto-resolv` | 41 | 1 | sonnet-5:41 | subagent:36, unknown:5 |
| `clodex-clodex.t568.hand` | 41 | 1 | opus-5:41 | parent:40, unknown:1 |
| `clodex-clodex.t618.hand` | 41 | 1 | opus-5:41 | parent:40, unknown:1 |
| `clodex-clodex.t640.hand` | 41 | 1 | opus-5:41 | parent:40, unknown:1 |
| `clodex-fable-audit-team` | 40 | 1 | fable-5:40 | parent:39, unknown:1 |
| `clodex-clodex.t505.review-r2` | 40 | 1 | opus-5:40 | parent:39, unknown:1 |
| `clodex-clodex-hand-551b` | 40 | 1 | opus-5:40 | parent:39, unknown:1 |
| `clodex-clodex.t606.review-r3` | 40 | 1 | opus-5:40 | parent:39, unknown:1 |
| `clodex-clodex.t611.hand` | 40 | 1 | opus-5:40 | parent:39, unknown:1 |
| `clodex-clodex.t622.hand` | 40 | 1 | opus-5:40 | parent:39, unknown:1 |
| `clodex-clodex.t643.hand` | 40 | 1 | opus-5:40 | parent:39, unknown:1 |
| `clodex-clodex.t497.hand` | 39 | 1 | opus-5:39 | parent:38, unknown:1 |
| `clodex-clodex.t500.hand` | 39 | 1 | opus-5:39 | parent:38, unknown:1 |
| `crypto-maple` | 39 | 1 | sonnet-5:39 | subagent:34, unknown:5 |
| `clodex-clodex.t510.hand` | 39 | 1 | opus-5:39 | parent:38, unknown:1 |
| `clodex-clodex.t603.review-r1` | 39 | 1 | opus-5:39 | parent:38, unknown:1 |
| `clodex-clodex.t605.hand` | 39 | 1 | opus-5:39 | parent:38, unknown:1 |
| `clodex-clodex.t621.hand` | 39 | 1 | opus-5:39 | parent:38, unknown:1 |
| `clodex-clodex.t629.hand` | 39 | 1 | opus-5:39 | parent:38, unknown:1 |
| `clodex-clodex.t182.hand` | 38 | 1 | opus-5:38 | parent:37, unknown:1 |
| `crypto-streamflow` | 38 | 1 | sonnet-5:38 | subagent:33, unknown:5 |
| `clodex-clodex.t559.review-r1` | 38 | 1 | opus-5:38 | parent:37, unknown:1 |
| `clodex-clodex.t565.hand` | 38 | 1 | opus-5:38 | parent:37, unknown:1 |
| `crypto-aave` | 38 | 1 | sonnet-5:38 | subagent:33, unknown:5 |
| `clodex-clodex.t600.review-r1` | 38 | 2 | opus-5:38 | parent:36, unknown:2 |
| `crypto-velo` | 38 | 1 | sonnet-5:38 | subagent:33, unknown:5 |
| `clodex-clodex.t625.hand` | 38 | 1 | opus-5:38 | parent:37, unknown:1 |
| `clodex-clodex.t631.hand` | 38 | 1 | opus-5:38 | parent:37, unknown:1 |
| `clodex-clodex.t672.review-r1` | 38 | 1 | opus-5:38 | parent:37, unknown:1 |
| `clodex-clodex.t351.review-r2` | 37 | 1 | opus-5:37 | parent:36, unknown:1 |
| `crypto-clearpool` | 37 | 1 | sonnet-5:37 | subagent:32, unknown:5 |
| `clodex-clodex.t493.hand` | 37 | 1 | opus-5:37 | parent:36, unknown:1 |
| `clodex-clodex.t506.hand` | 37 | 1 | opus-5:37 | parent:36, unknown:1 |
| `clodex-clodex.t523.hand` | 37 | 1 | opus-5:37 | parent:36, unknown:1 |
| `clodex-clodex.t533.review-r1` | 37 | 1 | opus-5:37 | parent:36, unknown:1 |
| `clodex-clodex.t511.review-r1` | 37 | 1 | opus-5:37 | parent:36, unknown:1 |
| `clodex-clodex.t665.hand` | 37 | 1 | opus-5:37 | parent:36, unknown:1 |
| `clodex-clodex.t442.hand` | 36 | 1 | opus-5:36 | parent:35, unknown:1 |
| `clodex-clodex.t509.review-r5` | 36 | 1 | opus-5:36 | parent:35, unknown:1 |
| `clodex-clodex.t581.review-r1` | 36 | 1 | opus-5:36 | parent:35, unknown:1 |
| `clodex-clodex.t605.review-r1` | 36 | 1 | opus-5:36 | parent:35, unknown:1 |
| `clodex-clodex.t369.hand` | 35 | 1 | opus-5:35 | parent:34, unknown:1 |
| `clodex-clodex.t480.hand` | 35 | 1 | opus-5:35 | parent:34, unknown:1 |
| `clodex-clodex.t489.review-r2` | 35 | 1 | opus-5:35 | parent:34, unknown:1 |
| `clodex-clodex.t490.review-r2` | 35 | 1 | opus-5:35 | parent:34, unknown:1 |
| `crypto-propy` | 35 | 1 | sonnet-5:35 | subagent:30, unknown:5 |
| `clodex-clodex.t522.review-r1` | 35 | 2 | opus-5:35 | parent:33, unknown:2 |
| `clodex-clodex.t712.hand` | 35 | 1 | opus-5:35 | parent:34, unknown:1 |
| `clodex-clodex-designer-473` | 34 | 1 | fable-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t482.review-r1` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t486.review-r1` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t505.review-r3` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t546.hand` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t547.hand` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t598.review-r1` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t603.review-r2` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t607.review-r3` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t617.hand` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t635.review-r1` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t654.review-r1` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t658.hand` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t666.hand` | 34 | 1 | opus-5:34 | parent:33, unknown:1 |
| `clodex-clodex.t524.hand` | 33 | 1 | opus-5:33 | parent:32, unknown:1 |
| `clodex-clodex.t512.hand` | 33 | 1 | opus-5:33 | parent:32, unknown:1 |
| `clodex-clodex.t543.hand` | 33 | 1 | opus-5:33 | parent:32, unknown:1 |
| `crypto-goldfinch` | 33 | 1 | sonnet-5:33 | subagent:28, unknown:5 |
| `clodex-clodex.t612.hand` | 33 | 1 | opus-5:33 | parent:32, unknown:1 |
| `clodex-clodex.t623.review-r1` | 33 | 1 | opus-5:33 | parent:32, unknown:1 |
| `clodex-clodex.t675.review-r1` | 33 | 1 | opus-5:33 | parent:32, unknown:1 |
| `clodex-clodex.t711.hand` | 33 | 1 | opus-5:33 | parent:32, unknown:1 |
| `clodex-clodex.t443.review-r1` | 32 | 1 | opus-5:32 | parent:31, unknown:1 |
| `clodex-clodex.t458.review-r1` | 32 | 1 | opus-5:32 | parent:31, unknown:1 |
| `clodex-clodex.t491.review-r1` | 32 | 1 | opus-5:32 | parent:31, unknown:1 |
| `clodex-clodex.t537.hand` | 32 | 1 | opus-5:32 | parent:31, unknown:1 |
| `clodex-clodex.t540.review-r1` | 32 | 1 | opus-5:32 | parent:31, unknown:1 |
| `clodex-clodex.t518.review-r2` | 32 | 1 | opus-5:32 | parent:31, unknown:1 |
| `clodex-clodex.t581.review-r3` | 32 | 1 | opus-5:32 | parent:31, unknown:1 |
| `clodex-clodex.t618.review-r1` | 32 | 1 | opus-5:32 | parent:31, unknown:1 |
| `crypto-babylon` | 32 | 1 | sonnet-5:32 | subagent:27, unknown:5 |
| `clodex-clodex.t662.review-r1` | 32 | 1 | opus-5:32 | parent:31, unknown:1 |
| `clodex-clodex.t447.review-r1` | 31 | 1 | opus-5:31 | parent:30, unknown:1 |
| `clodex-clodex.t482.review-r2` | 31 | 1 | opus-5:31 | parent:30, unknown:1 |
| `clodex-clodex.t504.review-r2` | 31 | 1 | opus-5:31 | parent:30, unknown:1 |
| `clodex-clodex.t532.review-r6` | 31 | 1 | opus-5:31 | parent:30, unknown:1 |
| `clodex-clodex.t555.review-r1` | 31 | 1 | opus-5:31 | parent:30, unknown:1 |
| `clodex-clodex.t659.review-r1` | 31 | 1 | opus-5:31 | parent:30, unknown:1 |
| `clodex-clodex.t661.review-r2` | 31 | 1 | opus-5:31 | parent:30, unknown:1 |
| `clodex-clodex.t463.review-r1` | 30 | 1 | opus-5:30 | parent:29, unknown:1 |
| `clodex-clodex.t495.hand` | 30 | 1 | opus-5:30 | parent:29, unknown:1 |
| `clodex-clodex.t572.review-r3` | 30 | 1 | opus-5:30 | parent:29, unknown:1 |
| `clodex-clodex.t606.review-r2` | 30 | 1 | opus-5:30 | parent:29, unknown:1 |
| `clodex-clodex.t633.hand` | 30 | 1 | opus-5:30 | parent:29, unknown:1 |
| `clodex-clodex.t636.review-r1` | 30 | 1 | opus-5:30 | parent:29, unknown:1 |
| `clodex-clodex.t660.review-r1` | 30 | 1 | opus-5:30 | parent:29, unknown:1 |
| `clodex-clodex.t351.review-r1` | 29 | 1 | opus-5:29 | parent:28, unknown:1 |
| `clodex-clodex.t460.review-r1` | 29 | 1 | opus-5:29 | parent:28, unknown:1 |
| `clodex-clodex.t456.review-r1` | 29 | 1 | opus-5:29 | parent:28, unknown:1 |
| `clodex-clodex.t183.hand` | 29 | 1 | opus-5:29 | parent:28, unknown:1 |
| `clodex-clodex.t498.review-r1` | 29 | 1 | opus-5:29 | parent:28, unknown:1 |
| `clodex-clodex.t545.hand` | 29 | 1 | opus-5:29 | parent:28, unknown:1 |
| `clodex-clodex.t654.review-r2` | 29 | 1 | opus-5:29 | parent:28, unknown:1 |
| `clodex-clodex.t453.review-r2` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t345.review-r2` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t378.review-r2` | 28 | 2 | opus-5:28 | parent:26, unknown:2 |
| `clodex-clodex.t475.hand` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t488.review-r1` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t494.review-r3` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t517.review-r2` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t516.hand` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `brief-fluid` | 28 | 14 | haiku-4-5-20251001:28 | unknown:14, parent:14 |
| `clodex-clodex.t551.review-r2` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t556.review-r1` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t572.review-r1` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t594.review-r2` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t595.review-r2` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t600.review-r2` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t601.hand` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t602.review-r1` | 28 | 2 | opus-5:28 | parent:26, unknown:2 |
| `clodex-clodex.t634.review-r2` | 28 | 1 | opus-5:28 | parent:27, unknown:1 |
| `clodex-clodex.t323.review-r1` | 27 | 1 | opus-5:27 | parent:26, unknown:1 |
| `clodex-clodex.t518.review-r1` | 27 | 1 | opus-5:27 | parent:26, unknown:1 |
| `clodex-clodex.t511.review-r2` | 27 | 1 | opus-5:27 | parent:26, unknown:1 |
| `clodex-clodex.t556.review-r2` | 27 | 1 | opus-5:26, ?:1 | parent:25, unknown:1, (no-response-file):1 |
| `clodex-clodex.t566.review-r1` | 27 | 1 | opus-5:27 | parent:26, unknown:1 |
| `clodex-clodex.t598.review-r2` | 27 | 1 | opus-5:27 | parent:26, unknown:1 |
| `clodex-clodex.t582.review-r1` | 27 | 1 | opus-5:27 | parent:26, unknown:1 |
| `clodex-clodex.t637.review-r2` | 27 | 1 | opus-5:27 | parent:26, unknown:1 |
| `clodex-clodex.t706.hand` | 27 | 1 | opus-5:27 | parent:26, unknown:1 |
| `clodex-clodex.t470.review-r2` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t483.review-r1` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t484.review-r1` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t503.hand` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `brief-cow` | 26 | 13 | haiku-4-5-20251001:26 | unknown:13, parent:13 |
| `brief-ena` | 26 | 13 | haiku-4-5-20251001:26 | unknown:13, parent:13 |
| `clodex-clodex.t551.review-r1` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t585.review-r1` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t597.review-r3` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t620.review-r1` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t550.review-r2` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t639.review-r1` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t637.review-r1` | 26 | 1 | opus-5:26 | parent:25, unknown:1 |
| `clodex-clodex.t314.review-r1` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `crypto-daily-2026-08-25` | 25 | 1 | sonnet-5:14, ?:11 | unknown:14, (no-response-file):11 |
| `clodex-clodex.t484.review-r2` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t507.review-r1` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t508.hand` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t509.review-r2` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t519.hand` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t534.review-r1` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t536.review-r3` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t571.review-r2` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t615.hand` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t633.review-r1` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t638.review-r1` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t654.review-r3` | 25 | 1 | opus-5:25 | parent:24, unknown:1 |
| `clodex-clodex.t463.hand` | 24 | 1 | opus-5:24 | parent:23, unknown:1 |
| `clodex-clodex.t491.review-r2` | 24 | 1 | opus-5:24 | parent:23, unknown:1 |
| `clodex-clodex.t499.hand` | 24 | 1 | opus-5:24 | parent:23, unknown:1 |
| `clodex-clodex.t501.hand` | 24 | 1 | opus-5:24 | parent:23, unknown:1 |
| `clodex-clodex.t495.review-r1` | 24 | 1 | opus-5:24 | parent:23, unknown:1 |
| `clodex-clodex.t524.review-r1` | 24 | 1 | opus-5:24 | parent:23, unknown:1 |
| `clodex-clodex.t536.review-r1` | 24 | 1 | opus-5:24 | parent:23, unknown:1 |
| `brief-par` | 24 | 12 | haiku-4-5-20251001:24 | unknown:12, parent:12 |
| `brief-aave` | 24 | 12 | haiku-4-5-20251001:24 | unknown:12, parent:12 |
| `brief-pro` | 24 | 12 | haiku-4-5-20251001:24 | unknown:12, parent:12 |
| `clodex-clodex.t555.hand` | 24 | 1 | opus-5:24 | parent:23, unknown:1 |
| `clodex-clodex.t580.hand` | 24 | 1 | opus-5:24 | parent:23, unknown:1 |
| `clodex-clodex.t714.hand` | 24 | 1 | opus-5:23, ?:1 | parent:22, unknown:1, (no-response-file):1 |
| `clodex-clodex.t357.review-r1` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t357.review-r2` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t449.review-r1` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t451.review-r2` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t314.review-r3` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t455.review-r1` | 23 | 2 | opus-5:23 | parent:21, unknown:2 |
| `clodex-clodex.t459.review-r1` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t464.review-r1` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t482.review-r4` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t517.review-r1` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t527.hand` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `brief-resolv` | 23 | 11 | haiku-4-5-20251001:23 | parent:12, unknown:11 |
| `brief-spk` | 23 | 11 | haiku-4-5-20251001:23 | parent:12, unknown:11 |
| `clodex-clodex.t549.review-r3` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t586.hand` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t587.review-r3` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t614.review-r3` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t479.review-r1` | 23 | 1 | opus-5:23 | parent:22, unknown:1 |
| `clodex-clodex.t448.review-r1` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t464.review-r2` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t465.review-r2` | 22 | 2 | opus-5:22 | parent:20, unknown:2 |
| `clodex-clodex.t494.review-r2` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t499.review-r1` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `brief-baby` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-cfg` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-drv` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-eul` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-gfi` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-gmx` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-kmno` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-ldo` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-morpho` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-ssv` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-syrup` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-tru` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-uni` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `brief-well` | 22 | 11 | haiku-4-5-20251001:22 | unknown:11, parent:11 |
| `clodex-clodex.t549.review-r2` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t549.review-r4` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t570.review-r1` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t571.review-r1` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t575.hand` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t606.review-r1` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t614.review-r5` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t561.review-r2` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t641.review-r1` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t644.review-r1` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t668.review-r1` | 22 | 1 | opus-5:22 | parent:21, unknown:1 |
| `clodex-clodex.t445.review-r2` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t183.review-r1` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t182.review-r1` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t472.review-r4` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t482.review-r3` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t573.review-r1` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t573.review-r2` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t624.review-r1` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t646.review-r1` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t659.review-r2` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t666.review-r1` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t672.review-r2` | 21 | 1 | opus-5:21 | parent:20, unknown:1 |
| `clodex-clodex.t445.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t451.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t455.hand` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t484.review-r4` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t506.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t532.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t532.review-r4` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t535.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t534.review-r2` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t536.review-r4` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t549.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t558.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t564.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t569.review-r2` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t579.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t601.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t611.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t612.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t614.review-r4` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t619.review-r1` | 20 | 2 | opus-5:20 | parent:18, unknown:2 |
| `clodex-clodex.t550.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t634.review-r3` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t649.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t651.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t663.review-r1` | 20 | 1 | opus-5:20 | parent:19, unknown:1 |
| `clodex-clodex.t442.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex-designer-4` | 19 | 1 | fable-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t472.review-r2` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t472.review-r7` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t489.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t492.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t504.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t509.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t525.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t531.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t526.hand` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t536.review-r2` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t538.review-r3` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t542.review-r3` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t544.review-r2` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t596.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t608.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t610.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t628.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t630.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t658.review-r1` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t657.review-r3` | 19 | 1 | opus-5:19 | parent:18, unknown:1 |
| `clodex-clodex.t345.review-r3` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t502.hand` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t497.review-r1` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t535.review-r2` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t542.review-r1` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `brief-jup` | 18 | 9 | haiku-4-5-20251001:18 | unknown:9, parent:9 |
| `brief-ondo` | 18 | 9 | haiku-4-5-20251001:18 | unknown:9, parent:9 |
| `brief-huma` | 18 | 9 | haiku-4-5-20251001:18 | unknown:9, parent:9 |
| `clodex-clodex.t511.review-r5` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t593.review-r1` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t594.review-r1` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t599.review-r2` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t604.review-r1` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t607.review-r2` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t609.review-r1` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t561.review-r1` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t648.review-r1` | 18 | 1 | opus-5:18 | parent:17, unknown:1 |
| `clodex-clodex.t378.review-r1` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex-designer-audit` | 17 | 1 | fable-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t494.review-r1` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t532.review-r2` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t521.review-r3` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t542.review-r2` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t541.review-r4` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t511.review-r3` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t554.review-r1` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t553.review-r1` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t587.review-r1` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t621.review-r1` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t619.review-r3` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t616.review-r1` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t643.review-r1` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t649.review-r2` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex-designer-653` | 17 | 1 | fable-5-1:17 | parent:15, unknown:2 |
| `clodex-clodex.t671.review-r2` | 17 | 1 | opus-5:17 | parent:16, unknown:1 |
| `clodex-clodex.t450.review-r2` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t471.review-r1` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t502.review-r1` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t504.review-r3` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t505.review-r1` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t507.hand` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t513.review-r2` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t528.hand` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t530.hand` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t538.review-r1` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t566.review-r2` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `brief-chex` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-cpool` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-drift` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-giza` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-hype` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-link` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-mamo` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-pendle` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-sei` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-snx` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-stream` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-synai` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-trade` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-velo` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `brief-zbcn` | 16 | 8 | haiku-4-5-20251001:16 | unknown:8, parent:8 |
| `clodex-clodex.t577.review-r3` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t571.review-r3` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t586.review-r1` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t587.review-r2` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t602.review-r2` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t607.review-r1` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t614.review-r1` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t650.review-r1` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t650.review-r2` | 16 | 1 | opus-5:16 | parent:15, unknown:1 |
| `clodex-clodex.t452.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t452.review-r2` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t470.review-r4` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `crypto-macro-2026-08-25` | 15 | 1 | sonnet-5:15 | unknown:15 |
| `clodex-clodex.t484.review-r3` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t490.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t538.review-r2` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t545.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t544.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t511.review-r4` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t564.review-r2` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t569.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t578.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t578.review-r2` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t587.review-r4` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t592.hand` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t604.review-r2` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t613.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t625.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t627.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t632.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t657.review-r2` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t671.review-r1` | 15 | 1 | opus-5:15 | parent:14, unknown:1 |
| `clodex-clodex.t314.review-r2` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t462.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `crypto-daily-2026-08-21` | 14 | 1 | sonnet-5:14 | unknown:14 |
| `clodex-clodex.t466.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t472.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t472.review-r6` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `crypto-macro-2026-08-23` | 14 | 1 | sonnet-5:14 | unknown:14 |
| `clodex-fable-audit-network` | 14 | 1 | opus-5:9, fable-5:4, opus-4-8:1 | parent:10, unknown:2, subagent:2 |
| `clodex-clodex.t512.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t537.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t539.hand` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t521.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t520.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t547.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t562.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t577.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t580.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t572.review-r2` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t592.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t604.review-r3` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t621.review-r2` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t616.review-r2` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t626.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t634.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `crypto-daily-2026-09-03` | 14 | 1 | sonnet-5:14 | unknown:14 |
| `clodex-clodex.t642.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t657.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t664.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `clodex-clodex.t665.review-r1` | 14 | 1 | opus-5:14 | parent:13, unknown:1 |
| `crypto-daily-2026-08-20` | 13 | 1 | sonnet-5:13 | unknown:13 |
| `auto-3075-w1` | 13 | 1 | sonnet-4-6:13 | parent:12, unknown:1 |
| `crypto-daily-2026-08-24` | 13 | 1 | sonnet-5:13 | unknown:13 |
| `crypto-macro-2026-08-26` | 13 | 1 | sonnet-5:13 | unknown:13 |
| `clodex-clodex.t509.review-r3` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `clodex-clodex.t514.hand` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `clodex-clodex.t527.review-r1` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `clodex-clodex.t526.review-r1` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `clodex-clodex.t536.review-r6` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `clodex-clodex.t521.review-r2` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `clodex-clodex.t541.review-r1` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `crypto-daily-2026-08-29` | 13 | 1 | sonnet-5:13 | unknown:13 |
| `crypto-daily-2026-08-30` | 13 | 1 | sonnet-5:13 | unknown:13 |
| `clodex-clodex.t615.review-r1` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `clodex-clodex.t638.review-r2` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `clodex-clodex.t651.review-r2` | 13 | 1 | opus-5:13 | parent:12, unknown:1 |
| `crypto-daily-2026-09-05` | 13 | 1 | sonnet-5:13 | unknown:13 |
| `clodex-clodex.t704.review-r1` | 13 | 1 | fable-5-1:13 | parent:12, unknown:1 |
| `clodex-Analyst` | 12 | 12 | fable-5:12 | unknown:12 |
| `crypto-macro-2026-08-20` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `clodex-clodex.t456.review-r2` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `crypto-macro-2026-08-21` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `clodex-clodex.t369.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `clodex-clodex.t389.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `clodex-clodex.t472.review-r5` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `crypto-daily-2026-08-23` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `clodex-clodex.t503.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `clodex-clodex.t519.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `clodex-clodex.t515.hand` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `clodex-clodex.t516.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `clodex-clodex.t529.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `clodex-clodex.t541.review-r2` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `crypto-macro-2026-08-28` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `clodex-clodex.t560.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `crypto-macro-2026-08-30` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `clodex-clodex.t568.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `clodex-clodex.t575.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `crypto-macro-2026-08-31` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `crypto-daily-2026-08-31` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `crypto-macro-2026-09-01` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `clodex-clodex.t614.review-r2` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `crypto-daily-2026-09-02` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `crypto-macro-2026-09-03` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `clodex-clodex.t645.review-r2` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `clodex-clodex.t647.review-r1` | 12 | 1 | opus-5:12 | parent:11, unknown:1 |
| `crypto-macro-2026-09-04` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `crypto-macro-2026-09-05` | 12 | 1 | sonnet-5:12 | unknown:12 |
| `clodex-clodex.t366.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t466.review-r2` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `crypto-daily-2026-08-22` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `clodex-clodex.t471.review-r3` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `crypto-daily-2026-08-26` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `clodex-clodex.t503.review-r2` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t496.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t500.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `crypto-macro-2026-08-27` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `crypto-daily-2026-08-27` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `clodex-clodex.t514.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t528.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t530.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t537.review-r2` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t536.review-r5` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t543.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t546.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t546.review-r2` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t548.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `crypto-daily-2026-08-28` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `crypto-macro-2026-08-29` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `clodex-clodex.t569.review-r3` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t577.review-r2` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `crypto-daily-2026-09-01` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `clodex-clodex.t619.review-r2` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `crypto-macro-2026-09-02` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `clodex-clodex.t617.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t582.review-r2` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t629.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `crypto-daily-2026-09-04` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `clodex-clodex.t649.review-r3` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `clodex-clodex.t669.review-r1` | 11 | 1 | opus-5:11 | parent:10, unknown:1 |
| `crypto-macro-2026-09-06` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `crypto-daily-2026-09-06` | 11 | 1 | sonnet-5:11 | unknown:11 |
| `clodex-clodex.t700.review-r1` | 11 | 1 | fable-5-1:7, ?:4 | parent:6, (no-response-file):4, unknown:1 |
| `clodex-clodex.t443.review-r2` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `crypto-macro-2026-08-22` | 10 | 1 | sonnet-5:10 | unknown:10 |
| `clodex-clodex.t472.review-r8` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t475.review-r1` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t493.review-r1` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t501.review-r1` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t509.review-r4` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t496.review-r2` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t510.review-r1` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t515.review-r1` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t532.review-r3` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t532.review-r5` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t539.review-r1` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t628.review-r2` | 10 | 2 | opus-5:10 | parent:8, unknown:2 |
| `clodex-clodex.t667.review-r1` | 10 | 1 | opus-5:10 | parent:9, unknown:1 |
| `clodex-clodex.t685.review-r1` | 10 | 1 | fable-5-1:10 | parent:9, unknown:1 |
| `clodex-clodex.t461.review-r1` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `clodex-clodex.t378.review-r3` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `crypto-macro-2026-08-24` | 9 | 1 | sonnet-5:9 | unknown:9 |
| `clodex-clodex.t508.review-r1` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `clodex-clodex.t548.review-r3` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `clodex-clodex.t567.review-r1` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `clodex-clodex.t627.review-r2` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `clodex-clodex.t631.review-r1` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `clodex-clodex.t648.review-r2` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `clodex-clodex.t650.review-r3` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `clodex-clodex.t663.review-r2` | 9 | 1 | opus-5:9 | parent:8, unknown:1 |
| `clodex-clodex.t676.review-r1` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t675.review-r2` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t679.review-r1` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t679.review-r2` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t680.review-r1` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t681.review-r1` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t685.review-r2` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t694.review-r1` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t703.review-r1` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t709.review-r1` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t710.review-r1` | 9 | 1 | fable-5-1:9 | parent:8, unknown:1 |
| `clodex-clodex.t462.review-r2` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `clodex-clodex.t472.review-r3` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `clodex-clodex.t472.review-r9` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `clodex-clodex.t474.review-r1` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `clodex-clodex.t474.review-r2` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `clodex-clodex.t474.review-r3` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `clodex-clodex.t513.review-r1` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `clodex-clodex.t541.review-r3` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `ab-compact-sonnet` | 8 | 8 | sonnet-5:8 | parent:8 |
| `clodex-clodex.t622.review-r1` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `clodex-clodex.t635.review-r2` | 8 | 1 | opus-5:8 | parent:7, unknown:1 |
| `clodex-clodex.t677.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t678.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t683.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t684.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t686.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t689.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t690.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t691.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t692.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t699.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t701.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t705.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t707.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t708.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t712.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `clodex-clodex.t711.review-r1` | 8 | 1 | fable-5-1:8 | parent:7, unknown:1 |
| `oracle` | 7 | 1 | sonnet-5:7 | parent:7 |
| `clodex-clodex.t471.review-r2` | 7 | 1 | opus-5:7 | parent:6, unknown:1 |
| `clodex-clodex.t523.review-r1` | 7 | 1 | opus-5:7 | parent:6, unknown:1 |
| `clodex-clodex.t543.review-r2` | 7 | 1 | opus-5:7 | parent:6, unknown:1 |
| `clodex-clodex.t548.review-r2` | 7 | 1 | opus-5:7 | parent:6, unknown:1 |
| `clodex-clodex.t565.review-r1` | 7 | 1 | opus-5:7 | parent:6, unknown:1 |
| `clodex-clodex.t670.review-r1` | 7 | 1 | opus-5:7 | parent:6, unknown:1 |
| `clodex-clodex.t673.review-r2` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t674.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t678.review-r2` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t682.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-pluginx` | 7 | 1 | opus-5:7 | parent:6, unknown:1 |
| `clodex-clodex.t683.review-r2` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t687.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t688.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t691.review-r2` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t693.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t696.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t697.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t698.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t702.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t704.review-r2` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t713.review-r1` | 7 | 1 | fable-5-1:7 | parent:6, unknown:1 |
| `clodex-clodex.t640.review-r1` | 6 | 1 | opus-5:6 | parent:5, unknown:1 |
| `clodex-clodex.t673.review-r1` | 6 | 1 | fable-5-1:6 | parent:5, unknown:1 |
| `clodex-clodex.t674.review-r2` | 6 | 1 | fable-5-1:6 | parent:5, unknown:1 |
| `clodex-clodex.t683.review-r3` | 6 | 1 | fable-5-1:6 | parent:5, unknown:1 |
| `clodex-clodex.t695.review-r1` | 6 | 1 | fable-5-1:6 | parent:5, unknown:1 |
| `clodex-clodex.t697.review-r2` | 6 | 1 | fable-5-1:6 | parent:5, unknown:1 |
| `clodex-clodex.t699.review-r2` | 6 | 1 | fable-5-1:6 | parent:5, unknown:1 |
| `clodex-clodex.t702.review-r2` | 6 | 1 | fable-5-1:6 | parent:5, unknown:1 |
| `clodex-clodex.t706.review-r1` | 6 | 1 | fable-5-1:6 | parent:5, unknown:1 |
| `clodex-clodex.t707.review-r2` | 6 | 1 | fable-5-1:6 | parent:5, unknown:1 |
| `clodex-clodex.t682.review-r2` | 5 | 1 | fable-5-1:5 | parent:4, unknown:1 |
| `clodex-clodex.t712.review-r2` | 5 | 1 | fable-5-1:5 | parent:4, unknown:1 |
| `repo-2` | 2 | 2 | sonnet-5:2 | parent:2 |
| `repo-3` | 2 | 2 | sonnet-5:2 | parent:2 |
| `repo-1` | 2 | 2 | sonnet-5:2 | parent:2 |
| `ws-teapot-base` | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-strict` | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-relaxed` | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-off0` | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-offfalse` | 2 | 1 | sonnet-5:2 | unknown:1, parent:1 |
| `ws-teapot-o-base` | 2 | 1 | opus-5:2 | unknown:2 |
| `ws-teapot-o-off0` | 2 | 1 | opus-5:2 | unknown:2 |
| `ws-teapot-o-relaxed` | 2 | 1 | opus-5:2 | unknown:2 |
| `clodex-clodex.t465.review-r3` | 1 | 1 | opus-5:1 | unknown:1 |
| `executor-1` | 1 | 1 | sonnet-4-6:1 | unknown:1 |
| `executor-2` | 1 | 1 | sonnet-4-6:1 | unknown:1 |
| `ctx` | 1 | 1 | fable-5-1:1 | unknown:1 |

## Q2. `clodex-clodex-*` sessions (all roles in the dir that carry the clodex-clodex agent name)

| session | agent hash(es) | first ts | last ts | requests | parent reqs | opus-5 | fable-5-1 | other |
|---|---|---|---|---|---|---|---|---|
| `d48e988f-1527-4e62-bd22-39a250538c43` | ab58afb7 | 2026-08-20T00:34:15 | 2026-08-23T16:29:13 | 4293 | 3961 | 3997 | 0 | sonnet-5:290, ?:6 |
| `0731c55e-ac6a-4670-ac8d-453bbd44f0fd` | 1171978b | 2026-08-23T16:32:50 | 2026-08-27T01:53:25 | 1380 | 1362 | 1375 | 0 | ?:5 |
| `35ec3085-6a07-439b-89c9-9715cb54ebdc` | e904b573 | 2026-08-27T01:54:13 | 2026-08-27T01:54:30 | 5 | 4 | 5 | 0 |  |
| `4d74dc16-1f2b-4f1f-8f9f-8c3289fb93b0` | 382d6149 | 2026-08-27T01:54:40 | 2026-09-03T11:23:47 | 2901 | 2862 | 2894 | 0 | ?:7 |
| `7a549dc5-9f87-4958-874b-94062f503f02` | 08cfc6c9 | 2026-08-30T00:22:51 | 2026-09-02T02:59:13 | 127 | 126 | 127 | 0 |  |
| `37464a95-c823-4c5c-9c3a-53aee81e58b7` | 831cac31 | 2026-08-30T01:23:06 | 2026-09-06T23:31:31 | 4843 | 4538 | 4590 | 0 | sonnet-5:249, ?:4 |
| `ebaeb02e-cae1-4df2-8c18-1ece18d6d476` | 831cac31 | 2026-09-03T22:46:25 | 2026-09-03T22:46:28 | 3 | 2 | 0 | 0 | haiku-4-5-20251001:3 |
| `1d21dd12-87cf-4583-9908-413e25614658` | 831cac31 | 2026-09-03T22:46:48 | 2026-09-03T22:46:51 | 3 | 2 | 0 | 0 | haiku-4-5-20251001:3 |
| `d4831a4a-6e8d-4c65-8dd1-995b9140121b` | 831cac31 | 2026-09-03T22:48:13 | 2026-09-03T22:48:15 | 3 | 2 | 0 | 0 | haiku-4-5-20251001:3 |
| `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | 5653f2b5, cc567364 | 2026-09-04T08:13:07 | 2026-09-06T23:50:35 | 3018 | 2775 | 879 | 2060 | haiku-4-5-20251001:19, sonnet-5:24, opus-4-8:33, ?:3 |
| `513e438d-6bb7-4c30-890e-14942371c39c` | cc567364 | 2026-09-04T09:49:06 | 2026-09-04T09:49:09 | 3 | 2 | 0 | 0 | haiku-4-5-20251001:3 |
| `31fce304-50d1-4334-b273-7a5840f31706` | cc567364 | 2026-09-04T09:49:28 | 2026-09-04T09:49:31 | 3 | 2 | 0 | 0 | haiku-4-5-20251001:3 |
| `7d5f2456-a1cb-4a4d-9ed7-2ddbe8e9d202` | cc567364 | 2026-09-04T09:50:02 | 2026-09-04T09:50:05 | 3 | 2 | 0 | 0 | haiku-4-5-20251001:3 |

### Model switches, main seat, role=parent only (consecutive parent requests, chronological across sessions)

| first request on new model | session | from | to | previous parent request ts (session) |
|---|---|---|---|---|
| 2026-09-03T22:46:25 | `ebaeb02e-cae1-4df2-8c18-1ece18d6d476` | opus-5 | haiku-4-5-20251001 | 2026-09-03T22:46:03 (`37464a95`) |
| 2026-09-03T22:46:30 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | haiku-4-5-20251001 | opus-5 | 2026-09-03T22:46:28 (`ebaeb02e`) |
| 2026-09-03T22:46:48 | `1d21dd12-87cf-4583-9908-413e25614658` | opus-5 | haiku-4-5-20251001 | 2026-09-03T22:46:30 (`37464a95`) |
| 2026-09-03T22:46:54 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | haiku-4-5-20251001 | opus-5 | 2026-09-03T22:46:51 (`1d21dd12`) |
| 2026-09-03T22:48:13 | `d4831a4a-6e8d-4c65-8dd1-995b9140121b` | opus-5 | haiku-4-5-20251001 | 2026-09-03T22:47:34 (`37464a95`) |
| 2026-09-03T22:48:20 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | haiku-4-5-20251001 | opus-5 | 2026-09-03T22:48:15 (`d4831a4a`) |
| 2026-09-04T09:49:06 | `513e438d-6bb7-4c30-890e-14942371c39c` | opus-5 | haiku-4-5-20251001 | 2026-09-04T09:49:02 (`5383fbbc`) |
| 2026-09-04T09:49:12 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | haiku-4-5-20251001 | opus-5 | 2026-09-04T09:49:09 (`513e438d`) |
| 2026-09-04T09:49:28 | `31fce304-50d1-4334-b273-7a5840f31706` | opus-5 | haiku-4-5-20251001 | 2026-09-04T09:49:12 (`5383fbbc`) |
| 2026-09-04T09:49:37 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | haiku-4-5-20251001 | opus-5 | 2026-09-04T09:49:31 (`31fce304`) |
| 2026-09-04T09:50:02 | `7d5f2456-a1cb-4a4d-9ed7-2ddbe8e9d202` | opus-5 | haiku-4-5-20251001 | 2026-09-04T09:49:44 (`5383fbbc`) |
| 2026-09-04T09:50:09 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | haiku-4-5-20251001 | opus-5 | 2026-09-04T09:50:05 (`7d5f2456`) |
| 2026-09-04T23:29:26 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-04T23:27:47 (`5383fbbc`) |
| 2026-09-05T00:02:16 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-04T23:58:47 (`5383fbbc`) |
| 2026-09-05T00:16:09 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T00:02:16 (`37464a95`) |
| 2026-09-05T00:58:16 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T00:58:11 (`5383fbbc`) |
| 2026-09-05T00:58:36 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T00:58:16 (`37464a95`) |
| 2026-09-05T01:11:23 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | fable-5-1 | opus-4-8 | 2026-09-05T01:06:30 (`5383fbbc`) |
| 2026-09-05T01:54:16 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | opus-4-8 | opus-5 | 2026-09-05T01:50:05 (`5383fbbc`) |
| 2026-09-05T02:23:13 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T01:54:16 (`37464a95`) |
| 2026-09-05T02:49:37 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T02:48:55 (`5383fbbc`) |
| 2026-09-05T03:02:35 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T02:49:37 (`37464a95`) |
| 2026-09-05T03:45:37 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T03:43:51 (`5383fbbc`) |
| 2026-09-05T03:48:56 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T03:45:37 (`37464a95`) |
| 2026-09-05T04:41:37 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T04:02:37 (`5383fbbc`) |
| 2026-09-05T04:58:37 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T04:41:37 (`37464a95`) |
| 2026-09-05T05:37:37 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T04:58:37 (`5383fbbc`) |
| 2026-09-05T05:54:37 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T05:37:37 (`37464a95`) |
| 2026-09-05T06:33:37 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T05:54:37 (`5383fbbc`) |
| 2026-09-05T06:50:37 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T06:33:37 (`37464a95`) |
| 2026-09-05T07:29:37 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T06:50:37 (`5383fbbc`) |
| 2026-09-05T07:46:37 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T07:29:37 (`37464a95`) |
| 2026-09-05T08:25:16 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T08:09:27 (`5383fbbc`) |
| 2026-09-05T08:58:07 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T08:25:16 (`37464a95`) |
| 2026-09-05T09:21:00 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T09:14:00 (`5383fbbc`) |
| 2026-09-05T09:21:17 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T09:21:00 (`37464a95`) |
| 2026-09-05T10:17:00 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T10:16:47 (`5383fbbc`) |
| 2026-09-05T10:28:49 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T10:17:00 (`37464a95`) |
| 2026-09-05T11:13:00 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T11:10:36 (`5383fbbc`) |
| 2026-09-05T11:15:32 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T11:13:00 (`37464a95`) |
| 2026-09-05T12:08:11 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T12:07:33 (`5383fbbc`) |
| 2026-09-05T12:11:59 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T12:08:11 (`37464a95`) |
| 2026-09-05T13:04:11 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T12:59:04 (`5383fbbc`) |
| 2026-09-05T13:05:01 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T13:04:11 (`37464a95`) |
| 2026-09-05T14:00:10 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T13:58:28 (`5383fbbc`) |
| 2026-09-05T14:36:30 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T14:00:10 (`37464a95`) |
| 2026-09-05T14:56:10 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T14:54:39 (`5383fbbc`) |
| 2026-09-05T14:56:40 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T14:56:10 (`37464a95`) |
| 2026-09-05T15:52:10 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T15:51:44 (`5383fbbc`) |
| 2026-09-05T15:54:57 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T15:52:10 (`37464a95`) |
| 2026-09-05T16:48:11 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T16:48:07 (`5383fbbc`) |
| 2026-09-05T16:48:13 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T16:48:11 (`37464a95`) |
| 2026-09-05T17:43:35 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T17:41:39 (`5383fbbc`) |
| 2026-09-05T17:46:42 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T17:43:35 (`37464a95`) |
| 2026-09-05T18:39:35 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T18:36:46 (`5383fbbc`) |
| 2026-09-05T18:49:10 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T18:39:35 (`37464a95`) |
| 2026-09-05T19:35:35 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T19:35:27 (`5383fbbc`) |
| 2026-09-05T19:35:46 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T19:35:35 (`37464a95`) |
| 2026-09-05T20:31:21 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T20:24:34 (`5383fbbc`) |
| 2026-09-05T20:48:52 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T20:31:21 (`37464a95`) |
| 2026-09-05T21:26:55 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T21:23:59 (`5383fbbc`) |
| 2026-09-05T21:32:03 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T21:26:55 (`37464a95`) |
| 2026-09-05T22:22:55 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T22:19:21 (`5383fbbc`) |
| 2026-09-05T22:45:39 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T22:22:55 (`37464a95`) |
| 2026-09-05T23:18:55 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-05T23:18:15 (`5383fbbc`) |
| 2026-09-05T23:20:22 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-05T23:18:55 (`37464a95`) |
| 2026-09-06T00:14:37 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T00:12:05 (`5383fbbc`) |
| 2026-09-06T00:18:28 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T00:14:37 (`37464a95`) |
| 2026-09-06T01:10:22 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T01:10:07 (`5383fbbc`) |
| 2026-09-06T01:10:25 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T01:10:22 (`37464a95`) |
| 2026-09-06T02:06:24 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T01:54:28 (`5383fbbc`) |
| 2026-09-06T02:18:11 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T02:06:24 (`37464a95`) |
| 2026-09-06T03:02:24 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T02:51:03 (`5383fbbc`) |
| 2026-09-06T03:03:55 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T03:02:24 (`37464a95`) |
| 2026-09-06T03:58:24 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T03:58:16 (`5383fbbc`) |
| 2026-09-06T03:58:28 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T03:58:24 (`37464a95`) |
| 2026-09-06T04:54:24 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T04:54:18 (`5383fbbc`) |
| 2026-09-06T04:54:31 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T04:54:24 (`37464a95`) |
| 2026-09-06T05:50:24 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T05:37:16 (`5383fbbc`) |
| 2026-09-06T05:57:11 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T05:50:24 (`37464a95`) |
| 2026-09-06T06:46:24 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T06:18:18 (`5383fbbc`) |
| 2026-09-06T06:53:57 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T06:46:24 (`37464a95`) |
| 2026-09-06T07:42:24 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T07:36:03 (`5383fbbc`) |
| 2026-09-06T08:31:24 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T07:42:24 (`37464a95`) |
| 2026-09-06T08:38:24 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T08:31:24 (`5383fbbc`) |
| 2026-09-06T08:51:48 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T08:38:24 (`37464a95`) |
| 2026-09-06T09:33:56 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T09:27:31 (`5383fbbc`) |
| 2026-09-06T09:34:39 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T09:33:56 (`37464a95`) |
| 2026-09-06T10:30:12 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T10:25:45 (`5383fbbc`) |
| 2026-09-06T10:36:57 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T10:30:12 (`37464a95`) |
| 2026-09-06T11:25:43 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T11:22:05 (`5383fbbc`) |
| 2026-09-06T11:29:54 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T11:25:43 (`37464a95`) |
| 2026-09-06T12:21:43 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T12:21:21 (`5383fbbc`) |
| 2026-09-06T12:26:25 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T12:21:43 (`37464a95`) |
| 2026-09-06T13:17:43 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T12:56:53 (`5383fbbc`) |
| 2026-09-06T13:52:43 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T13:17:43 (`37464a95`) |
| 2026-09-06T14:13:43 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T13:52:43 (`5383fbbc`) |
| 2026-09-06T14:48:43 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T14:13:43 (`37464a95`) |
| 2026-09-06T15:09:43 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T14:48:43 (`5383fbbc`) |
| 2026-09-06T15:15:35 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T15:09:43 (`37464a95`) |
| 2026-09-06T16:05:43 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T15:22:32 (`5383fbbc`) |
| 2026-09-06T16:17:43 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T16:05:43 (`37464a95`) |
| 2026-09-06T17:01:43 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T16:43:54 (`5383fbbc`) |
| 2026-09-06T17:04:08 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T17:01:43 (`37464a95`) |
| 2026-09-06T17:57:02 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T17:53:30 (`5383fbbc`) |
| 2026-09-06T17:59:15 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T17:57:02 (`37464a95`) |
| 2026-09-06T18:52:43 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T18:45:00 (`5383fbbc`) |
| 2026-09-06T18:52:50 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T18:52:43 (`37464a95`) |
| 2026-09-06T19:48:43 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T19:48:31 (`5383fbbc`) |
| 2026-09-06T19:48:46 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T19:48:43 (`37464a95`) |
| 2026-09-06T20:43:48 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T20:40:08 (`5383fbbc`) |
| 2026-09-06T20:44:03 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T20:43:48 (`37464a95`) |
| 2026-09-06T21:39:32 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T21:36:06 (`5383fbbc`) |
| 2026-09-06T21:39:42 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T21:39:32 (`37464a95`) |
| 2026-09-06T22:35:33 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T22:31:09 (`5383fbbc`) |
| 2026-09-06T22:35:54 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T22:35:33 (`37464a95`) |
| 2026-09-06T23:31:31 | `37464a95-c823-4c5c-9c3a-53aee81e58b7` | fable-5-1 | opus-5 | 2026-09-06T23:28:03 (`5383fbbc`) |
| 2026-09-06T23:31:37 | `5383fbbc-5a73-4e63-b6b6-86d4c6739f96` | opus-5 | fable-5-1 | 2026-09-06T23:31:31 (`37464a95`) |

### Per-session parent-model timeline (first/last parent ts per model within each session)

| session | model | first parent ts | last parent ts | parent reqs |
|---|---|---|---|---|
| `d48e988f` | opus-5 | 2026-08-20T00:34:15 | 2026-08-23T11:45:53 | 3961 |
| `0731c55e` | opus-5 | 2026-08-23T16:33:31 | 2026-08-27T01:53:25 | 1362 |
| `35ec3085` | opus-5 | 2026-08-27T01:54:16 | 2026-08-27T01:54:30 | 4 |
| `4d74dc16` | opus-5 | 2026-08-27T01:54:56 | 2026-09-03T11:23:47 | 2862 |
| `7a549dc5` | opus-5 | 2026-08-30T00:23:10 | 2026-09-02T02:59:13 | 126 |
| `37464a95` | opus-5 | 2026-08-30T01:23:12 | 2026-09-06T23:31:31 | 4538 |
| `ebaeb02e` | haiku-4-5-20251001 | 2026-09-03T22:46:25 | 2026-09-03T22:46:28 | 2 |
| `1d21dd12` | haiku-4-5-20251001 | 2026-09-03T22:46:48 | 2026-09-03T22:46:51 | 2 |
| `d4831a4a` | haiku-4-5-20251001 | 2026-09-03T22:48:13 | 2026-09-03T22:48:15 | 2 |
| `5383fbbc` | opus-5 | 2026-09-04T08:13:21 | 2026-09-04T23:27:47 | 705 |
| `5383fbbc` | fable-5-1 | 2026-09-04T23:29:26 | 2026-09-06T23:50:35 | 2037 |
| `5383fbbc` | opus-4-8 | 2026-09-05T01:11:23 | 2026-09-05T01:50:05 | 33 |
| `513e438d` | haiku-4-5-20251001 | 2026-09-04T09:49:06 | 2026-09-04T09:49:09 | 2 |
| `31fce304` | haiku-4-5-20251001 | 2026-09-04T09:49:28 | 2026-09-04T09:49:31 | 2 |
| `7d5f2456` | haiku-4-5-20251001 | 2026-09-04T09:50:02 | 2026-09-04T09:50:05 | 2 |

## Q3. Main seat per calendar day (all roles / parent only)

| day | requests | parent reqs | opus-5 (all/parent) | fable-5-1 (all/parent) | other (all) | sessions |
|---|---|---|---|---|---|---|
| 2026-08-20 | 736 | 627 | 630/627 | 0/0 | sonnet-5:106 | 1 |
| 2026-08-21 | 2420 | 2391 | 2420/2391 | 0/0 |  | 1 |
| 2026-08-22 | 1117 | 930 | 933/930 | 0/0 | sonnet-5:184 | 1 |
| 2026-08-23 | 126 | 112 | 116/112 | 0/0 | ?:10 | 2 |
| 2026-08-24 | 166 | 166 | 166/166 | 0/0 |  | 1 |
| 2026-08-25 | 236 | 234 | 236/234 | 0/0 |  | 1 |
| 2026-08-26 | 647 | 642 | 646/642 | 0/0 | ?:1 | 1 |
| 2026-08-27 | 1585 | 1561 | 1585/1561 | 0/0 |  | 3 |
| 2026-08-28 | 1073 | 1063 | 1072/1063 | 0/0 | ?:1 | 1 |
| 2026-08-29 | 356 | 346 | 350/346 | 0/0 | ?:6 | 1 |
| 2026-08-30 | 621 | 594 | 614/594 | 0/0 | sonnet-5:7 | 3 |
| 2026-08-31 | 1247 | 1111 | 1119/1111 | 0/0 | sonnet-5:128 | 3 |
| 2026-09-01 | 1212 | 1087 | 1094/1087 | 0/0 | sonnet-5:114, ?:4 | 3 |
| 2026-09-02 | 1156 | 1151 | 1156/1151 | 0/0 |  | 3 |
| 2026-09-03 | 618 | 603 | 609/597 | 0/0 | haiku-4-5-20251001:9 | 5 |
| 2026-09-04 | 960 | 913 | 901/894 | 14/13 | haiku-4-5-20251001:21, sonnet-5:24 | 5 |
| 2026-09-05 | 1171 | 1137 | 44/26 | 1087/1078 | haiku-4-5-20251001:7, opus-4-8:33 | 2 |
| 2026-09-06 | 1138 | 972 | 176/26 | 959/946 | ?:3 | 2 |

## Q4. Roles on the main seat

Role counts for agent prefix `clodex-clodex` (from `.response.json` `role`): {'parent': 15640, 'unknown': 136, 'subagent': 644, '(no-response-file)': 25, 'general-purpose': 140}

- parent: {'opus-5': 13558, 'haiku-4-5-20251001': 12, 'fable-5-1': 2037, 'opus-4-8': 33}
- unknown: {'opus-5': 107, 'haiku-4-5-20251001': 6, 'fable-5-1': 23}
- subagent: {'sonnet-5': 442, 'opus-5': 202}
- (no-response-file): {'?': 25}
- general-purpose: {'sonnet-5': 121, 'haiku-4-5-20251001': 19}

Other agent prefixes/roles whose captures live in a main-seat session dir: {('ext', '(no-response-file)'): 4, ('ab-compact-sonnet', 'parent'): 4}

Main-seat rows whose response `session_id` differs from the directory name: 0


## Script

```python
import os, re, json, collections, sys
ROOT = "/Users/bogdan/Library/Application Support/clodex/wirescope/logs/"
LO, HI = "2026-08-20", "2026-09-06"
name_re = re.compile(r"^(\d+)-(.+)-(\d{6})\.request\.json$")
ts_re = re.compile(r'"ts":\s*"([^"]+)"'); agent_re = re.compile(r'"agent":\s*"([^"]*)"')
role_re = re.compile(r'"role":\s*"([^"]*)"'); model_re = re.compile(r'"model":\s*"([^"]*)"')
sid_re = re.compile(r'"session_id":\s*"([^"]*)"')
def short(m): return m.replace("claude-","") if m else m
rows = []  # dict per request
noresp = 0
for sess in os.listdir(ROOT):
    d = os.path.join(ROOT, sess)
    if not os.path.isdir(d) or len(sess) != 36: continue
    try: names = os.listdir(d)
    except Exception: continue
    for n in names:
        m = name_re.match(n)
        if not m: continue
        try:
            with open(os.path.join(d, n), "rb") as f: head = f.read(600).decode("utf-8","replace")
        except Exception: continue
        tm = ts_re.search(head); ts = tm.group(1) if tm else None
        if ts is None or not (LO <= ts[:10] <= HI): continue
        am = agent_re.search(head); agent = am.group(1) if am else "?"
        rp = os.path.join(d, n[:-len(".request.json")] + ".response.json")
        role = model = sid = None
        try:
            with open(rp, "rb") as f: rh = f.read(400).decode("utf-8","replace")
            rm = role_re.search(rh); role = rm.group(1) if rm else "(none)"
            mm = model_re.search(rh); model = short(mm.group(1)) if mm else "?"
            sm = sid_re.search(rh); sid = sm.group(1) if sm else None
        except Exception:
            noresp += 1; role = "(no-response-file)"; model = "?"
        prefix = re.sub(r"-[0-9a-f]{8}$", "", agent)
        rows.append(dict(sess=sess, seq=int(m.group(1)), ts=ts, agent=agent, prefix=prefix, role=role, model=model, sid=sid))
rows.sort(key=lambda r: (r["ts"], r["seq"]))
out = []; P = out.append
P(f"# Inventory: clodex wirescope captures {LO}..{HI}\n")
P(f"Total request captures in window: {len(rows)} (requests lacking a .response.json: {noresp}). role/model read from `.response.json` header; ts/agent from `.request.json` header; session = directory name.\n")
def family(p):
    p = re.sub(r"^clodex-clodex\.t\d+\.(review-r\d+|hand|[a-z-]+)$", lambda m: "clodex-clodex.t*."+re.sub(r"\d+$","N",m.group(1)), p)
    p = re.sub(r"^crypto-(daily|macro)-\d{4}-\d\d-\d\d$", r"crypto-\1-<date>", p)
    p = re.sub(r"^crypto-[a-z_0-9]+$", "crypto-<token>", p)
    p = re.sub(r"^clodex-clodex-hand-[0-9a-f]+$", "clodex-clodex-hand-<hex>", p)
    p = re.sub(r"^clodex-clodex-designer-\d+$", "clodex-clodex-designer-<n>", p)
    p = re.sub(r"^auto-\d+-w\d+$", "auto-<n>-w<n>", p)
    return p
P("## Q1a. Agent FAMILIES (per-task prefixes collapsed): request count, sessions, models, roles\n")
P("| family | distinct prefixes | requests | session dirs | models | roles |\n|---|---|---|---|---|---|")
byf = collections.defaultdict(list)
for r in rows: byf[family(r["prefix"])].append(r)
for p, rs in sorted(byf.items(), key=lambda kv: -len(kv[1])):
    models = collections.Counter(r["model"] for r in rs); roles = collections.Counter(r["role"] for r in rs)
    P(f"| `{p}` | {len(set(r['prefix'] for r in rs))} | {len(rs)} | {len(set(r['sess'] for r in rs))} | {', '.join(f'{m}:{c}' for m,c in models.most_common())} | {', '.join(f'{k}:{v}' for k,v in roles.most_common())} |")
P("\n## Q1b. Every distinct agent prefix (full list)\n")
P("| prefix | requests | session dirs | models | roles |\n|---|---|---|---|---|")
byp = collections.defaultdict(list)
for r in rows: byp[r["prefix"]].append(r)
for p, rs in sorted(byp.items(), key=lambda kv: -len(kv[1])):
    models = collections.Counter(r["model"] for r in rs); roles = collections.Counter(r["role"] for r in rs)
    P(f"| `{p}` | {len(rs)} | {len(set(r['sess'] for r in rs))} | {', '.join(f'{m}:{c}' for m,c in models.most_common())} | {', '.join(f'{k}:{v}' for k,v in roles.most_common())} |")
main = [r for r in rows if r["prefix"] == "clodex-clodex"]
P("\n## Q2. `clodex-clodex-*` sessions (all roles in the dir that carry the clodex-clodex agent name)\n")
P("| session | agent hash(es) | first ts | last ts | requests | parent reqs | opus-5 | fable-5-1 | other |\n|---|---|---|---|---|---|---|---|---|")
bys = collections.defaultdict(list)
for r in main: bys[r["sess"]].append(r)
for s, rs in sorted(bys.items(), key=lambda kv: kv[1][0]["ts"]):
    mc = collections.Counter(r["model"] for r in rs)
    other = ", ".join(f"{k}:{v}" for k,v in mc.items() if k not in ("opus-5","fable-5-1"))
    agents = sorted(set(r["agent"][-8:] for r in rs))
    P(f"| `{s}` | {', '.join(agents)} | {rs[0]['ts']} | {rs[-1]['ts']} | {len(rs)} | {sum(r['role']=='parent' for r in rs)} | {mc.get('opus-5',0)} | {mc.get('fable-5-1',0)} | {other} |")
P("\n### Model switches, main seat, role=parent only (consecutive parent requests, chronological across sessions)\n")
P("| first request on new model | session | from | to | previous parent request ts (session) |\n|---|---|---|---|---|")
prev = None
for r in [r for r in main if r["role"]=="parent"]:
    if prev and prev["model"] != r["model"]:
        P(f"| {r['ts']} | `{r['sess']}` | {prev['model']} | {r['model']} | {prev['ts']} (`{prev['sess'][:8]}`) |")
    prev = r
P("\n### Per-session parent-model timeline (first/last parent ts per model within each session)\n")
P("| session | model | first parent ts | last parent ts | parent reqs |\n|---|---|---|---|---|")
for s, rs in sorted(bys.items(), key=lambda kv: kv[1][0]["ts"]):
    pm = collections.defaultdict(list)
    for r in rs:
        if r["role"]=="parent": pm[r["model"]].append(r["ts"])
    for mdl, tss in sorted(pm.items(), key=lambda kv: kv[1][0]):
        P(f"| `{s[:8]}` | {mdl} | {tss[0]} | {tss[-1]} | {len(tss)} |")
P("\n## Q3. Main seat per calendar day (all roles / parent only)\n")
P("| day | requests | parent reqs | opus-5 (all/parent) | fable-5-1 (all/parent) | other (all) | sessions |\n|---|---|---|---|---|---|---|")
byd = collections.defaultdict(list)
for r in main: byd[r["ts"][:10]].append(r)
for d, rs in sorted(byd.items()):
    mc = collections.Counter(r["model"] for r in rs); pc = collections.Counter(r["model"] for r in rs if r["role"]=="parent")
    other = ", ".join(f"{k}:{v}" for k,v in mc.items() if k not in ("opus-5","fable-5-1"))
    P(f"| {d} | {len(rs)} | {sum(pc.values())} | {mc.get('opus-5',0)}/{pc.get('opus-5',0)} | {mc.get('fable-5-1',0)}/{pc.get('fable-5-1',0)} | {other} | {len(set(r['sess'] for r in rs))} |")
P("\n## Q4. Roles on the main seat\n")
rc = collections.Counter(r["role"] for r in main)
P(f"Role counts for agent prefix `clodex-clodex` (from `.response.json` `role`): {dict(rc)}\n")
for role in rc:
    mc = collections.Counter(r["model"] for r in main if r["role"]==role)
    P(f"- {role}: {dict(mc)}")
main_sess = set(r["sess"] for r in main)
shared = collections.Counter((r["prefix"], r["role"]) for r in rows if r["sess"] in main_sess and r["prefix"] != "clodex-clodex")
P(f"\nOther agent prefixes/roles whose captures live in a main-seat session dir: {dict(shared) or 'none'}\n")
mism = sum(1 for r in main if r["sid"] and r["sid"] != r["sess"])
P(f"Main-seat rows whose response `session_id` differs from the directory name: {mism}\n")
text = "\n".join(out)
open(sys.argv[1], "w").write(text + "\n\n## Script\n\n```python\n" + open(__file__).read() + "\n```\n")
print(f"rows={len(rows)} main={len(main)} lines={len(out)}")

```
