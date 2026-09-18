# Agent Chat Cost Tables

Estimated from thesis agent token summaries. Reasoning tokens are reported separately, but are included in output tokens and are not billed a second time.

## Pricing Configuration

| Setting | Value |
| --- | ---: |
| Model | gpt-5.5 |
| Input rate per 1M tokens | $5.00 |
| Cached input rate per 1M tokens | $0.50 |
| Output rate per 1M tokens | $30.00 |
| Long prompt pricing | disabled |
| Effective input rate per 1M tokens | $5.00 |
| Effective cached input rate per 1M tokens | $0.50 |
| Effective output rate per 1M tokens | $30.00 |

## Per-Chat Costs

| Thesis | Chat ID | Input | Cached Input | Output | Reasoning | Total Tokens | Cost | Cache Ratio | Reasoning Share | Effective $/1M |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| thesis_007 | 01a0abfe-0e8b-7af2-a798-65548afc8841 | 2,866,583 | 2,682,752 | 42,731 | 8,066 | 2,909,314 | $3.5425 | 93.59% | 18.88% | $1.22 |
| thesis_009 | 01a0ac1d-6742-74f0-9a3e-ef71820f9881 | 5,617,285 | 5,018,496 | 40,047 | 10,634 | 5,657,332 | $6.7046 | 89.34% | 26.55% | $1.19 |
| thesis_010 | 01a0ac4a-0361-74a3-9aa4-60905702716c | 1,585,897 | 1,459,712 | 28,702 | 9,409 | 1,614,599 | $2.2218 | 92.04% | 32.78% | $1.38 |
| thesis_011 | 01a0ac4a-bbb7-7083-bf0c-a230dd6c737e | 2,206,863 | 2,063,488 | 37,608 | 9,403 | 2,244,471 | $2.8769 | 93.50% | 25.00% | $1.28 |
| thesis_012 | 01a0ae85-d99a-7570-a050-39d806bd073f | 1,558,917 | 1,272,704 | 26,267 | 9,491 | 1,585,184 | $2.8554 | 81.64% | 36.13% | $1.80 |
| thesis_013 | 01a0ae90-86c5-7f10-ae2d-71b2fc9fda92 | 1,014,871 | 900,480 | 24,441 | 8,668 | 1,039,312 | $1.7554 | 88.73% | 35.46% | $1.69 |
| thesis_014 | 01a0ae98-4e92-76f1-bf7f-0b4f8910443e | 2,102,561 | 1,944,576 | 35,062 | 11,017 | 2,137,623 | $2.8141 | 92.49% | 31.42% | $1.32 |
| thesis_015 | 01a0ae9f-eb2a-7910-afe2-88b2d7d22387 | 1,063,025 | 929,024 | 28,507 | 6,394 | 1,091,532 | $1.9897 | 87.39% | 22.43% | $1.82 |
| thesis_017 | 01a0afb6-e671-76f2-95c4-d4345fc89a3e | 3,719,375 | 3,470,464 | 44,562 | 12,042 | 3,763,937 | $4.3166 | 93.31% | 27.02% | $1.15 |
| thesis_019 | 01a0b125-6140-7b12-a12b-b4e681858e37 | 4,329,396 | 4,089,856 | 39,605 | 7,983 | 4,369,001 | $4.4308 | 94.47% | 20.16% | $1.01 |

## Summary Statistics

| Statistic | Chats | Input | Cached Input | Output | Total Tokens | Cost | Cache Ratio | Reasoning Share | Effective $/1M |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Total | 10 | 26,064,773 | 23,831,552 | 347,532 | 26,412,305 | $33.5078 | 91.43% | 26.79% | $1.27 |
| Mean | 10 | 2,606,477 | 2,383,155 | 34,753 | 2,641,230 | $3.3508 | 90.65% | 27.58% | $1.39 |
| Median | 10 | 2,154,712 | 2,004,032 | 36,335 | 2,191,047 | $2.8661 | 92.26% | 26.79% | $1.30 |
| Min | 10 | 1,014,871 | 900,480 | 24,441 | 1,039,312 | $1.7554 | 81.64% | 18.88% | $1.01 |
| Max | 10 | 5,617,285 | 5,018,496 | 44,562 | 5,657,332 | $6.7046 | 94.47% | 36.13% | $1.82 |
