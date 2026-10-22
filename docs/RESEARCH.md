# Research and sources

Every clinical rule in Mizani and every number in our problem statement traces to a source listed here. Rule values live in `src/mizani/rules/sats_adult.yaml`, where each rule names its source (chart or manual page). We encode the published rules and cite them; we do not reproduce the SATS chart or manual.

## Triage scale: South African Triage Scale (SATS), adult
| Used for | Source |
|---|---|
| Adult TEWS bands (respiratory rate, heart rate, systolic BP, temperature, AVPU incl. "confused", mobility, trauma), TEWS to colour (0 to 2 green, 3 to 4 yellow, 5 to 6 orange, 7 or more red), the 39 adult discriminators, the additional investigations | [1] |
| Five-step process, target times to be seen (red immediately, orange < 10 min, yellow < 1 h, green < 4 h), senior clinician override, definitions (high-energy transfer, threatened limb, controlled haemorrhage, inhalation burn, pain grades), trauma = any injury in the past 48 hours | [2] |
| TEWS total range 0 to 17, TEWS-to-colour bands, discriminators upgrade the colour | [3] |

Where the 2012 manual [2] and the newer adult chart [1] differ (the manual also lists "eye injury" as very urgent and says "stabbed neck" where the chart says "stabbed neck or chest"), we follow the chart, the publisher's current version.

The wide limits that catch typos (for example a pulse of 900 or a temperature typed in °F) are data-entry checks written by us, not clinical thresholds, and are labelled that way in the rule pack.

## Interoperability: HL7 FHIR R4
| Used for | Source |
|---|---|
| Vital-sign LOINC codes and units: respiratory rate 9279-1 (/min), heart rate 8867-4 (/min), blood pressure panel 85354-9 with systolic 8480-6 (mm[Hg]), body temperature 8310-5 (Cel), oxygen saturation 2708-6 (%); category `vital-signs` | [4] |
| Encounter status `triaged` ("assessed for the priority of their treatment based on the severity of their condition") | [5] |
| Blood glucose 15074-8 (mmol/L) | [6] |
| Borrowed MCP server: tools `create`, `read`, `search`; configuration; Apache-2.0 | [7] |

## Problem statement
| Claim | Setting and sample | Source |
|---|---|---|
| 24.4% of adult patients under-triaged | 389 nursing notes, three urban private hospital emergency centres, South Africa | [8] |
| Nurses' SATS accuracy 34.2% (under-triage 36.3%, over-triage 29.5%) | 552 pre- and 1,608 post-implementation referral charts, three health centres, Beira, Mozambique | [9] |
| Emergency length of stay fell from 183.78 to 51.39 min after SATS (mortality 15.7% to 10.7%) | 115 pre- and 230 post-implementation patients, Suez Canal University Hospital, Egypt | [10] |
| Kenya's largest public hospital had no emergency-specific triage protocol before SATS | Kenyatta National Hospital, Nairobi | [11] |
| Nurses report difficulty interpreting TEWS | 14 SATS-trained emergency nurses, focus groups May 2023, Cape Coast Teaching Hospital, Ghana | [12] |
| Prehospital providers chose the correct SATS colour in 56.5% of vignettes (under-triage 29.5%) | 102 providers, 50 vignettes, Cape Town | [3] |

## References
1. South African Triage Group. Adult SATS chart. Emergency Medicine Society of South Africa (EMSSA); 2015. https://emssa.org.za/wp-content/uploads/2017/10/Adult-SATS-2015.pdf (accessed 2026-10-22)
2. South African Triage Group. The SATS training manual. EMSSA; 2012. https://emssa.org.za/wp-content/uploads/2017/10/SATS-Manual.pdf (accessed 2026-10-22)
3. Mould-Millman NK, Dixon JM, Burkholder T, et al. Validity and reliability of the South African Triage Scale in prehospital providers. BMC Emerg Med. 2021;21:8. doi:10.1186/s12873-021-00406-6
4. HL7 International. FHIR R4: Observation vital signs profile. https://hl7.org/fhir/R4/observation-vitalsigns.html (accessed 2026-10-22)
5. HL7 International. FHIR R4: EncounterStatus value set. https://hl7.org/fhir/R4/valueset-encounter-status.html (accessed 2026-10-22)
6. Regenstrief Institute. LOINC 15074-8: Glucose [Moles/volume] in Blood (example unit mmol/L). https://loinc.org/15074-8 (accessed 2026-10-22)
7. WSO2. fhir-mcp-server, release 0.10.0 (commit 78d750ba). Apache-2.0. https://github.com/wso2/fhir-mcp-server (accessed 2026-10-22)
8. Smith J, Filmalter C, Masenge A, Heyns T. The accuracy of nurse-led triage of adult patients in the emergency centre of urban private hospitals. Afr J Emerg Med. 2022. https://repository.up.ac.za/handle/2263/86108
9. Conti A, et al. Implementation of the South African Triage Scale (SATS) in a new ambulance system in Beira, Mozambique: a retrospective observational study. Int J Environ Res Public Health. 2022;19(16):10298. https://www.mdpi.com/1660-4601/19/16/10298
10. Elbaih AH, Elhadary GK, Elbahrawy MR, et al. Assessment of the patients' outcomes after implementation of South African triage scale in emergency department, Egypt. Chin J Traumatol. 2022;25(2):95-101. doi:10.1016/j.cjtee.2021.10.004
11. Wangara AA, Hunold KM, Leeper S, et al. Implementation and performance of the South African Triage Scale at Kenyatta National Hospital in Nairobi, Kenya. Int J Emerg Med. 2019;12. doi:10.1186/s12245-019-0221-3
12. Eyeson EB, Achempim-Ansong G, Blankson S, Dzramado VL. Effectiveness of the South African Triage Scale in improving patient outcomes: a qualitative exploration of Ghanian nurses' experiences. Afr J Emerg Med. 2026;16(2):100971. doi:10.1016/j.afjem.2026.100971
