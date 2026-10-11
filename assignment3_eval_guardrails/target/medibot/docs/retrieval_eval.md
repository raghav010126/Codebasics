# Retrieval evaluation

26 labelled queries (16 exact-term/code, 10 natural-language paraphrase), run as `admin`. Relevant = right document AND chunk contains the labelled substring. Top-10 candidates per method.

## All queries
| Method | Hit@1 | Hit@3 | MRR@10 |
|---|---|---|---|
| dense | 77% | 92% | 0.841 |
| sparse | 73% | 92% | 0.837 |
| hybrid | 85% | 96% | 0.892 |
| hybrid+rerank | 85% | 92% | 0.899 |

## Exact-term / code queries
| Method | Hit@1 | Hit@3 | MRR@10 |
|---|---|---|---|
| dense | 81% | 94% | 0.865 |
| sparse | 75% | 94% | 0.854 |
| hybrid | 88% | 100% | 0.917 |
| hybrid+rerank | 81% | 94% | 0.883 |

## Paraphrase queries
| Method | Hit@1 | Hit@3 | MRR@10 |
|---|---|---|---|
| dense | 70% | 90% | 0.803 |
| sparse | 70% | 90% | 0.808 |
| hybrid | 80% | 90% | 0.853 |
| hybrid+rerank | 90% | 90% | 0.925 |

## Per-query rank of first relevant chunk (— = not in top 10)

| Query | dense | bm25 | hybrid | hybrid+rerank |
|---|---|---|---|---|
| What does fault code F-12 mean on the infusion pump? | 1 | 1 | 1 | 1 |
| E-12 internal sensor failure action | 1 | 1 | 1 | 1 |
| E-11 vacuum pump failure SterilPro | 1 | 1 | 1 | 1 |
| RadiPro MX-150 F-02 DR panel not detected | 1 | 1 | 1 | 1 |
| SterilPro 3000 E-01 door seal gasket | 1 | 1 | 1 | 1 |
| ICD-10 I21.0 anterior wall STEMI package rate | 1 | 1 | 1 | 1 |
| J18.9 pneumonia unspecified package | — | 2 | 3 | 1 |
| Amoxicillin-Clavulanate standard dose | 2 | 1 | 1 | 2 |
| Vancomycin trough monitoring dose | 1 | 1 | 1 | 1 |
| Colistin CMO approval tier 4 | 1 | 2 | 1 | 1 |
| Glipizide second-line diabetes | 1 | 1 | 1 | 1 |
| CURB-65 severity score pneumonia | 1 | 1 | 1 | 1 |
| Metformin 500 mg BD first-line | 1 | 1 | 1 | 1 |
| PaCO2 pH ABG interpretation acidosis | 1 | 1 | 1 | 1 |
| BM-500 monitor alarm parameter defaults | 3 | 6 | 3 | 8 |
| claim rejection codes counter-response | 1 | 2 | 1 | 2 |
| How quickly must I request pre-auth for an emergency admission? | 1 | 1 | 1 | 1 |
| What do I do when the infusion pump says the drug library is out of date? | 5 | 4 | 5 | 4 |
| How often should a central line dressing be changed? | 1 | 1 | 1 | 1 |
| what size cannula for a baby under 5kg | 1 | 1 | 1 | 1 |
| When should I clean my hands around a patient? | 2 | 3 | 3 | 1 |
| How many days of earned leave do nurses get? | 1 | 1 | 1 | 1 |
| What percentage of salary goes to provident fund? | 1 | 1 | 1 | 1 |
| Which drug should a diabetic patient start with? | 1 | 2 | 1 | 1 |
| What dose of paracetamol can a baby under 5 kg have? | 1 | 1 | 1 | 1 |
| Who must approve restricted antibiotics? | 3 | 1 | 1 | 1 |
