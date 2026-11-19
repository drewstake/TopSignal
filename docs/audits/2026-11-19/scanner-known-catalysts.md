# Known-catalyst search benchmark

November 19, 2026 · TopSignal scanner on peteamdpc

**GPT-6.2 Sol cited a correct, verifiable primary source on 15 of 20 moves. GPT-6 Luna did so on seven.** Sol found every source Luna got right and eight more. Each model searched the same 20 moves, with the same public inputs and primary-source domains. Across the 13 distinct release times, Sol succeeded on at least one ticker for 11 releases, Luna for five. The sample contains two company announcements and 18 ticker reactions to market-wide releases. Its strongest evidence concerns macro searches.

| Measure | GPT-6 Luna | GPT-6.2 Sol |
| --- | ---: | ---: |
| Live API requests completed | 20 | 20 |
| Answers proposing a URL | 9 | 17 |
| Correct primary source available by the alert cutoff | 7/20 (35%) | 15/20 (75%) |
| Correct on company-specific moves | 0/2 | 1/2 |
| Correct on market-wide moves | 7/18 | 14/18 |
| Accepted by current production verifier | 0/20 | 0/20 |
| Input tokens (cached reads) | 192,313 (86,944) | 193,709 (84,569) |
| Output tokens | 4,847 | 3,830 |
| Web search calls reported by the API | 30 | 26 |
| Estimated token cost | $0.01400 | $0.26835 |
| Estimated search-tool cost | $0.30000 | $0.26000 |
| Estimated total | **$0.31400** | **$0.52835** |
| Estimated cost per move | 1.57¢ | 2.64¢ |
| Estimated cost per correct source | 4.49¢ | 3.52¢ |
| Charge recorded against the scanner budget | $0.321835 | $0.689035 |
| Median API response time | 5.80 s | 6.88 s |

The 40 requests cost an estimated **$0.84235** at standard list prices. Sol added **$0.21435** over 20 moves, or about **2.68¢ per additional correct answer** in this sample. TopSignal booked **$1.010870** because its spending ledger charges cached reads at the full input rate. Both estimates include all reported web search actions at one cent each. All 40 API responses reported the requested model and used the default tier. No 12-hour cache writes were reported. The production worker's summary and search configurations both read `gpt-6-luna` after the test. [OpenAI prices](https://developers.openai.com/api/docs/pricing)

## What counted as a hit

Before sending any requests, we froze the 20 public inputs, event times and source documents. A hit required a returned URL identifying the relevant first-party release, a checkable original or equivalent official edition, and an explanation tied to the correct event. Publication had to precede or coincide with the modeled alert at the close of the move's one-minute bar. Identifying a scheduled event from an old invitation, citing a different period, or returning an unverifiable link did not earn a hit. A search result that the model left out of its answer did not count. At two release times the answer key accepted either of two simultaneous official reports.

Historical market data came from Alpaca IEX. All 20 bars passed the regular-hours production detector, including a one-minute absolute move of at least 1%, at least 2× relative volume over 20 consecutive regular-session bars, and the 15-minute same-ticker cooldown. For a market-wide release, the bar also had to fall within the first five minutes and move with SPY, with either a SPY move of at least 0.25% or a same-direction SOXX move of at least 0.5%. These are observed market reactions around known releases. Other information can contribute to an individual stock's move.

The runs searched the November 19 web index retrospectively. Each model saw the ticker, company, observed move, relative volume, SOXX context when available, and historical cutoff. Each used low reasoning effort, an 800-token output limit, and a one-tool-call request limit. The API reported 56 web search call records across the 40 responses. The benchmark gave both models the same expanded access to primary economic and policy publishers and accepted macro passages without a company name. The benchmark settings applied only to these requests. The scores measure whether the cited source was public by the cutoff. Search responses arrived seconds after each replay request.

## The 20 paired searches

All dates and times are US Eastern. “Hit” is a verified source in the model's final answer. A dash means the model returned no source. Costs are estimates in US dollars. Repeated publication times on different tickers represent the same news event.

| # | Stock | Modeled alert (ET) | Stock move | Relative volume | Source time (ET) | Preselected primary source | Luna | Sol | Luna cost | Sol cost |
| ---: | --- | --- | ---: | ---: | --- | --- | --- | --- | ---: | ---: |
| 1 | AMD | 2025-08-22 10:01 | +1.24% | 3.37× | 10:00:00 | [Powell at Jackson Hole](https://www.federalreserve.gov/newsevents/speech/powell20250822a.htm) | — | Hit | $0.0115 | $0.0360 |
| 2 | INTC | 2025-08-22 10:01 | +1.10% | 2.76× | 10:00:00 | [Powell at Jackson Hole](https://www.federalreserve.gov/newsevents/speech/powell20250822a.htm) | — | Hit | $0.0206 | $0.0252 |
| 3 | NVDA | 2025-08-22 10:01 | +1.12% | 2.34× | 10:00:00 | [Powell at Jackson Hole](https://www.federalreserve.gov/newsevents/speech/powell20250822a.htm) | — | Hit | $0.0207 | $0.0256 |
| 4 | AMD | 2025-06-12 14:31 | +1.18% | 5.09× | 14:30:00 | [AMD launches new AI infrastructure](https://ir.amd.com/news-events/press-releases/detail/1255/amd-unveils-vision-for-an-open-ai-ecosystem-detailing-new-silicon-software-and-systems-at-advancing-ai-2025) | Event invitation | Hit | $0.0206 | $0.0321 |
| 5 | INTC | 2025-04-09 13:20 | +2.58% | 4.95× | 13:18:41 | [President announces tariff pause](https://truthsocial.com/@realDonaldTrump/posts/114309144289505174) | — | — | $0.0205 | $0.0304 |
| 6 | NVDA | 2025-04-09 13:20 | +3.18% | 2.01× | 13:18:41 | [President announces tariff pause](https://truthsocial.com/@realDonaldTrump/posts/114309144289505174) | — | — | $0.0211 | $0.0302 |
| 7 | TSM | 2025-04-09 13:20 | +2.02% | 5.55× | 13:18:41 | [President announces tariff pause](https://truthsocial.com/@realDonaldTrump/posts/114309144289505174) | — | — | $0.0211 | $0.0300 |
| 8 | INTC | 2025-03-11 10:01 | +1.01% | 3.49× | 10:00:00 | [January JOLTS report](https://www.bls.gov/news.release/archives/jolts_03112025.htm) | — | Hit | $0.0205 | $0.0216 |
| 9 | AVGO | 2025-03-05 10:01 | +1.08% | 2.81× | 10:00:00 | [February services PMI](https://www.prnewswire.com/news-releases/services-pmi-at-53-5-february-2025-services-ism-report-on-business-302392223.html) · [Factory orders](https://www.census.gov/manufacturing/m3/historical_data/pressreleases/prel/2025/jan25prel.pdf) | — | Broken PDF | $0.0205 | $0.0328 |
| 10 | INTC | 2025-03-05 10:01 | +1.26% | 2.66× | 10:00:00 | [February services PMI](https://www.prnewswire.com/news-releases/services-pmi-at-53-5-february-2025-services-ism-report-on-business-302392223.html) · [Factory orders](https://www.census.gov/manufacturing/m3/historical_data/pressreleases/prel/2025/jan25prel.pdf) | — | Hit | $0.0205 | $0.0222 |
| 11 | AVGO | 2024-09-18 14:01 | +1.37% | 6.79× | 14:00:00 | [FOMC decision](https://www.federalreserve.gov/newsevents/pressreleases/monetary20240918a.htm) | Hit | Hit | $0.0107 | $0.0257 |
| 12 | INTC | 2024-09-18 14:01 | +1.54% | 7.41× | 14:00:00 | [FOMC decision](https://www.federalreserve.gov/newsevents/pressreleases/monetary20240918a.htm) | Hit | Hit | $0.0107 | $0.0257 |
| 13 | NVDA | 2024-09-18 14:01 | +1.62% | 10.74× | 14:00:00 | [FOMC decision](https://www.federalreserve.gov/newsevents/pressreleases/monetary20240918a.htm) | Hit | Hit | $0.0106 | $0.0216 |
| 14 | INTC | 2024-09-04 10:01 | -1.06% | 6.77× | 10:00:00 | [July JOLTS report](https://www.bls.gov/news.release/archives/jolts_09042024.htm) · [Factory orders](https://www.census.gov/manufacturing/m3/historical_data/pressreleases/prel/2024/jul24prel.pdf) | — | Hit | $0.0206 | $0.0217 |
| 15 | INTC | 2024-02-21 11:31 | +1.76% | 29.74× | 11:30:00 | [Intel launches systems foundry](https://www.intc.com/news-events/press-releases/detail/1675/intel-launches-worlds-first-systems-foundry-designed-for) | Keynote preview | Keynote preview | $0.0107 | $0.0316 |
| 16 | NVDA | 2022-11-02 14:01 | +1.06% | 4.87× | 14:00:00 | [FOMC decision](https://www.federalreserve.gov/newsevents/pressreleases/monetary20221102a.htm) | Hit | Hit | $0.0107 | $0.0257 |
| 17 | NVDA | 2022-09-21 14:01 | -1.52% | 5.64× | 14:00:00 | [FOMC decision](https://www.federalreserve.gov/newsevents/pressreleases/monetary20220921a.htm) | Hit | Hit | $0.0106 | $0.0214 |
| 18 | NVDA | 2022-06-15 14:02 | -1.19% | 3.00× | 14:00:00 | [FOMC decision](https://www.federalreserve.gov/newsevents/pressreleases/monetary20220615a.htm) | Hit | Hit | $0.0106 | $0.0218 |
| 19 | NVDA | 2022-03-16 14:02 | -1.24% | 17.37× | 14:00:00 | [FOMC decision](https://www.federalreserve.gov/newsevents/pressreleases/monetary20220316a.htm) | Hit | Hit | $0.0106 | $0.0257 |
| 20 | AMD | 2021-12-15 14:04 | +1.34% | 5.56× | 14:00:00 | [FOMC decision](https://www.federalreserve.gov/newsevents/pressreleases/monetary20211215a.htm) | — | Hit | $0.0106 | $0.0213 |

## How the two scores diverged

- **AMD, June 12, 2025.** Sol cited the release published at 2:30 p.m. about AMD's AI infrastructure and MI350 accelerators. The bar closed at 2:31. Luna cited the April invitation to the June event. The production checker found Sol's exact passage on the issuer page, but its article parser extracted no publication time. [AMD's announcement](https://ir.amd.com/news-events/press-releases/detail/1255/amd-unveils-vision-for-an-open-ai-ecosystem-detailing-new-silicon-software-and-systems-at-advancing-ai-2025) · [Earlier invitation](https://ir.amd.com/news-events/press-releases/detail/1243/amd-announces-advancing-ai-2025)
- **Broadcom, March 5, 2025.** Sol correctly identified the February ISM report and its 53.5 reading. Its linked PDF returned HTTP 404 during verification. This case received no source credit. On Intel's move in the same minute, Sol cited the verifiable release distributed by ISM and earned a hit. [ISM release](https://www.prnewswire.com/news-releases/services-pmi-at-53-5-february-2025-services-ism-report-on-business-302392223.html)
- **Intel, February 21, 2024.** Both answers cited a preview of the IFS Direct Connect keynote. Neither supplied Intel's announcement of the systems foundry published at 11:30 a.m. [Intel's announcement](https://www.intc.com/news-events/press-releases/detail/1675/intel-launches-worlds-first-systems-foundry-designed-for)
- **Tariff pause, April 9, 2025.** Both models abstained on all three tickers despite the original post preceding the modeled alerts. The gold time conservatively rounds publication up to the next second. [Original presidential post](https://truthsocial.com/@realDonaldTrump/posts/114309144289505174) · [Archived posts](https://www.presidency.ucsb.edu/documents/truth-social-posts-april-9-2025)
- **Production acceptance.** Replaying all 40 answers through the unmodified production verifier produced zero attachable sources. Its trusted publishers and issuer-name quote rule exclude the macro evidence in this sample. Even a correct issuer URL can fail if the checker cannot extract the published time. The benchmark measured source finding separately so these rejections are visible.

The SHA-256 digests of the frozen public input and answer-key files were `dd511ab97a77c469a1e4fa2df7c93f07e5d372b99171de3e98b29c9b8e474b39` and `3cfb65228f778173b11fc16d3a2f10f57abe132f95ae1b78dfd5447867e3c85e`, respectively. The protected host benchmark directory retains the answer key and per-request receipts.
