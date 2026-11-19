# Sol 6.2 and Luna on 20 unexplained semiconductor moves

Ran on peteamdpc on November 19, 2026. Both models searched the same 20 moves. Neither produced a source that TopSignal could verify by the alert time. Luna proposed five leads, including one relevant follow-up. Sol proposed none and cost about 91% more per move. The production worker uses `gpt-6-luna` for summaries and for searches.

| Search outcome | GPT-6 Luna | GPT-6.2 Sol |
| --- | ---: | ---: |
| Completed requests | 20 | 20 |
| Proposed a source | 5 | 0 |
| Verified by the one-minute bar close | 0 | 0 |
| Verified with another 30 minutes allowed | 0 | 0 |
| Median API response time | 4.35 s | 5.15 s |
| Reported web search tool calls | 24 | 27 |
| Input tokens (cached reads) | 180,346 (84,721) | 174,233 (83,334) |
| Output tokens | 3,069 | 1,876 |
| Estimated token cost | $0.01210 | $0.21193 |
| Estimated search tool cost | $0.24000 | $0.27000 |
| Estimated cost for 20 moves | **$0.25210** | **$0.48193** |
| Estimated cost per move | $0.01260 | $0.02410 |
| Spending ledger for these requests | $0.259732 | $0.640263 |

Combined estimated cost was **$0.73402**. The scanner booked **$0.899995** against its cycle cap because it charges cached reads at the full input rate and rounds each request up to the next microdollar. The estimate uses the reported cached reads, 30-minute cache writes and output tokens at standard pricing, plus one cent per reported web search call. Each request specified a one-tool-call limit. The API returned 51 web search call records across the 40 responses, all included in both cost calculations. Invoice reconciliation may differ. [OpenAI pricing](https://developers.openai.com/api/docs/pricing)

## Review of the five proposed sources

- **8 · INTC, June 18, 2026, 3:50 p.m. ET.** Luna proposed an unreadable link about the Intel Foundry leadership appointment. Intel's investor relations index timestamps the release at 4:31 p.m., 41 minutes after the move began. [Intel release index](https://www.intc.com/news-events/press-releases?page=2&q=8697%2Bprocessor%2Bintel) · [Intel announcement](https://www.intel.com/content/www/us/en/newsroom/news/corporate/intel-announces-leadership-appointment-at-intel-foundry-to-accelerate-development-and-manufacturing.html)
- **13 · NVDA, March 16, 2026, 3:09 p.m. ET.** Both tools retrieved NVIDIA's Vera Rubin announcement. Only Luna proposed it, saying it preceded the move. NVIDIA's wire release is timestamped 3:28 p.m., 18 minutes after the modeled 3:10 alert. It provides relevant follow-up coverage. The scanner also could not verify the quote and time on the proposed URL, which differed from the retrieved URL by a tracking parameter. [NVIDIA's wire release](https://www.globenewswire.com/news-release/2026/03/16/3256638/0/en/NVIDIA-Vera-Rubin-Opens-Agentic-AI-Frontier.html)
- **16 · AMD, November 11, 2025, 1:40 p.m. ET.** Luna proposed AMD's Analyst Day release. The quote matched the issuer page, but the published release came at 4:30 p.m. The answer supplied no earlier timestamped statement that explained the 1:40 move. [AMD's wire release](https://www.globenewswire.com/news-release/2025/11/11/3185793/0/en/amd-unveils-strategy-to-lead-the-1-trillion-compute-market-and-accelerate-next-phase-of-growth.html)
- **17 · AMD, October 27, 2025, 11:00 a.m. ET.** Luna proposed the filing about the ZT Systems divestiture. AMD announced the deal closing at 8:40 a.m. and the SEC accepted the filing at 8:49 a.m. Both were already hours old. [AMD's wire release](https://www.globenewswire.com/news-release/2025/10/27/3174599/0/en/AMD-Completes-Divestiture-of-ZT-Systems-Data-Center-Infrastructure-Manufacturing-Business-to-Sanmina.html) · [SEC filing time](https://www.sec.gov/Archives/edgar/data/2488/000119312525250803/0001193125-25-250803-index.htm)
- **19 · AMD, October 6, 2025, 3:55 p.m. ET.** Luna raised dilution from the OpenAI warrant. The SEC accepted the filing at 7:04 a.m. The answer identified no fresh disclosure in the afternoon. [SEC filing time](https://www.sec.gov/Archives/edgar/data/2488/000119312525230895/0001193125-25-230895-index.htm)

## The matched cases

Times below are the starts of the one-minute bars in US Eastern time. The modeled alert is one minute later. Relative volume uses the previous 20 consecutive regular-session minutes. A dash in the lead column means Luna returned no proposed source. Sol returned no proposed source on every row. Case costs are estimates rounded to four decimal places.

| Case | Move start (ET) | Ticker | One-minute move | Relative volume | SOXX move | Luna lead | Luna cost | Sol cost |
| ---: | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| 1 | 2026-11-17 12:05 | AMD | +1.44% | 3.16× | +1.07% | — | $0.0111 | $0.0307 |
| 2 | 2026-11-05 15:55 | INTC | -1.17% | 2.64× | -0.88% | — | $0.0105 | $0.0205 |
| 3 | 2026-11-04 12:19 | INTC | +1.27% | 6.73× | -0.10% | — | $0.0105 | $0.0209 |
| 4 | 2026-10-30 15:59 | INTC | +1.15% | 3.52× | +0.16% | — | $0.0105 | $0.0120 |
| 5 | 2026-09-11 12:29 | INTC | -1.09% | 4.12× | -0.20% | — | $0.0105 | $0.0304 |
| 6 | 2026-07-31 10:11 | INTC | +1.01% | 2.20× | +0.56% | — | $0.0205 | $0.0309 |
| 7 | 2026-06-25 09:51 | INTC | -1.15% | 2.30× | -0.69% | — | $0.0105 | $0.0249 |
| 8 | 2026-06-18 15:50 | INTC | -1.83% | 4.26× | -0.50% | Intel Foundry appointment | $0.0107 | $0.0303 |
| 9 | 2026-06-12 10:52 | AMD | +1.01% | 2.50× | +0.78% | — | $0.0105 | $0.0199 |
| 10 | 2026-05-29 15:50 | AVGO | +1.09% | 7.49× | -0.14% | — | $0.0105 | $0.0205 |
| 11 | 2026-05-14 09:50 | INTC | -1.13% | 2.18× | -0.15% | — | $0.0105 | $0.0199 |
| 12 | 2026-05-08 12:48 | INTC | +1.78% | 33.53× | +0.14% | — | $0.0106 | $0.0204 |
| 13 | 2026-03-16 15:09 | NVDA | +1.69% | 8.29× | +0.46% | NVIDIA Vera Rubin | $0.0107 | $0.0201 |
| 14 | 2025-12-19 15:50 | AVGO | +1.52% | 7.18× | -0.01% | — | $0.0206 | $0.0200 |
| 15 | 2025-11-21 13:48 | NVDA | +1.46% | 10.05× | +0.82% | — | $0.0206 | $0.0201 |
| 16 | 2025-11-11 13:40 | AMD | +1.64% | 2.80× | +0.10% | AMD Analyst Day | $0.0110 | $0.0298 |
| 17 | 2025-10-27 11:00 | AMD | +1.16% | 16.75× | +0.04% | AMD divestiture | $0.0106 | $0.0299 |
| 18 | 2025-10-17 09:52 | INTC | +1.68% | 8.92× | +0.17% | — | $0.0105 | $0.0308 |
| 19 | 2025-10-06 15:55 | AMD | -1.04% | 2.28× | -0.09% | AMD warrant filing | $0.0107 | $0.0199 |
| 20 | 2025-10-03 15:37 | INTC | -1.19% | 26.70× | -0.08% | — | $0.0205 | $0.0299 |

## How the cases and sources were checked

The live scanner had no stored unexplained moves when the sample was frozen. We replayed completed, unadjusted Alpaca IEX one-minute bars from full trading days through November 18 using the production detector. A move required at least a 1% absolute change and 2× relative volume. We applied the 15-minute same-ticker cooldown, then screened for available ticker-matched material headlines and for archived Alpaca news from 30 minutes before the move through the bar close. Cases were at least 30 minutes apart, with at most two per trading day and one per ticker per day. IEX represents a single exchange. The live issuer and SEC feeds had limited historical depth. The Hunterbrook newsletter was available, but its separate website feed failed to parse.

Both models received the same production search prompt, public market context, allowed domains, reasoning setting and output limit. We ran one request per model per move. The scorer used the production rules for a tool-listed URL, a reproducible issuer-matched quote, a publisher timestamp and an explanation. We evaluated each returned answer twice, at the modeled alert and with up to 30 more minutes for follow-up. We independently checked the five proposed stories against issuer releases or SEC acceptance times.

This retrospective search used the November 19 web index. The sample had 11 INTC, five AMD, two AVGO and two NVDA moves. No TSM move made the sample. The results measure verified explanations delivered for these 20 hard cases. A sample with known timely announcements would also be needed to measure how often either model misses an available explanation.
