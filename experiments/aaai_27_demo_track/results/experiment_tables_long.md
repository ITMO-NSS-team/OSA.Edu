# Aggregate Experiment Tables

Macro-averaged across 10 theses.

## Experiment 1 - Claim Extraction

| Metric | OSA.Edu | Agent |
| --- | ---: | ---: |
| Source Precision (higher) | 0.483 | 0.899 |
| Source Recall (higher) | 0.953 | 0.591 |
| Source F1 (higher) | 0.637 | 0.694 |
| Decomposition Precision (higher) | 0.522 | 0.320 |
| Decomposition Recall (higher) | 0.589 | 0.220 |
| Decomposition F1 (higher) | 0.551 | 0.254 |
| Full semantic coverage (higher) | 0.759 | 0.818 |
| Partial-or-better coverage (higher) | 0.954 | 0.943 |
| Over-decomposition rate (lower) | 0.114 | 0.013 |
| Under-decomposition rate (lower) | 0.111 | 0.485 |

## Experiment 2 - Verification On Gold Claims

| Metric | OSA.Edu |
| --- | ---: |
| Accuracy (higher) | 0.856 |
| Macro-F1 (higher) | 0.728 |
| Balanced Accuracy (higher) | 0.743 |
| IMPLEMENTED Precision (higher) | 0.951 |
| IMPLEMENTED Recall (higher) | 0.876 |
| IMPLEMENTED F1 (higher) | 0.908 |
| False Implementation Rate (lower) | 0.290 |

## Experiment 3 - End-to-End

| Metric | OSA.Edu | Agent |
| --- | ---: | ---: |
| Implementation-rate MAE, pp (lower) | 10.76 | 12.25 |
| Median absolute error, pp (lower) | 10.84 | 10.24 |
| Mean signed error, pp | 0.75 | -6.68 |
| Weighted-score MAE, pp (lower) | 12.09 | 6.92 |
| Spearman rho (higher) | 0.794 | 0.867 |
| Mean claim-count deviation (lower) | 1.312 | 0.665 |
| Mean extracted claims | 345.10 | 44.30 |

## Appendix - Claim Extraction By Thesis

| Thesis | OSA Source F1 | Agent Source F1 | OSA Decomp F1 | Agent Decomp F1 | OSA Full Cov | Agent Full Cov | OSA Over Rate | Agent Over Rate | OSA Under Rate | Agent Under Rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| thesis_007 | 0.670 | 1.000 | 0.605 | 1.000 | 0.842 | 1.000 | 0.049 | 0.033 | 0.186 | 0.022 |
| thesis_009 | 0.616 | 0.645 | 0.323 | 0.347 | 0.573 | 0.818 | 0.267 | 0.000 | 0.053 | 0.341 |
| thesis_010 | 0.620 | 0.534 | 0.539 | 0.359 | 0.764 | 0.929 | 0.049 | 0.000 | 0.187 | 0.554 |
| thesis_011 | 0.528 | 0.678 | 0.576 | 0.121 | 0.732 | 0.700 | 0.109 | 0.033 | 0.014 | 0.489 |
| thesis_012 | 0.554 | 0.755 | 0.667 | 0.115 | 0.795 | 0.968 | 0.077 | 0.000 | 0.103 | 0.710 |
| thesis_013 | 0.734 | 0.716 | 0.715 | 0.152 | 0.887 | 0.822 | 0.104 | 0.000 | 0.122 | 0.658 |
| thesis_014 | 0.665 | 0.714 | 0.532 | 0.080 | 0.658 | 0.794 | 0.130 | 0.007 | 0.078 | 0.706 |
| thesis_015 | 0.554 | 0.828 | 0.559 | 0.200 | 0.848 | 0.727 | 0.076 | 0.023 | 0.190 | 0.500 |
| thesis_017 | 0.700 | 0.544 | 0.508 | 0.113 | 0.741 | 0.723 | 0.163 | 0.010 | 0.064 | 0.416 |
| thesis_019 | 0.732 | 0.523 | 0.491 | 0.047 | 0.751 | 0.701 | 0.121 | 0.026 | 0.113 | 0.453 |

## Appendix - Verification By Thesis

| Thesis | Accuracy | Macro-F1 | Balanced Acc | Impl Precision | Impl Recall | Impl F1 | False Impl Rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| thesis_007 | 0.903 | 0.902 | 0.900 | 0.849 | 0.988 | 0.913 | 0.188 |
| thesis_009 | 0.820 | 0.705 | 0.836 | 0.978 | 0.815 | 0.889 | 0.143 |
| thesis_010 | 0.860 | 0.846 | 0.875 | 0.957 | 0.835 | 0.892 | 0.086 |
| thesis_011 | 0.881 | 0.469 | 0.447 | 0.984 | 0.895 | 0.937 | 1.000 |
| thesis_012 | 0.737 | 0.424 | 0.368 | 1.000 | 0.737 | 0.849 | 0.000 |
| thesis_013 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |
| thesis_014 | 0.946 | 0.942 | 0.938 | 0.943 | 0.971 | 0.957 | 0.094 |
| thesis_015 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |
| thesis_017 | 0.710 | 0.468 | 0.468 | 0.871 | 0.787 | 0.827 | 0.852 |
| thesis_019 | 0.704 | 0.521 | 0.594 | 0.929 | 0.730 | 0.817 | 0.542 |

## Appendix - End-to-End By Thesis

| Thesis | Gold Rate | OSA Rate | Agent Rate | OSA Abs Err pp | Agent Abs Err pp | OSA Signed pp | Agent Signed pp | OSA Weighted Err pp | Agent Weighted Err pp | OSA Count Dev | Agent Count Dev |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| thesis_007 | 0.465 | 0.633 | 0.204 | 16.84 | 26.04 | 16.84 | -26.04 | 11.92 | 5.45 | 0.727 | 0.732 |
| thesis_009 | 0.675 | 0.785 | 0.743 | 10.96 | 6.79 | 10.96 | 6.79 | 0.91 | 6.34 | 2.037 | 0.568 |
| thesis_010 | 0.608 | 0.582 | 0.735 | 2.54 | 12.76 | -2.54 | 12.76 | 8.69 | 16.90 | 0.831 | 0.750 |
| thesis_011 | 0.899 | 0.923 | 0.787 | 2.45 | 11.14 | 2.45 | -11.14 | 1.95 | 4.90 | 1.755 | 0.682 |
| thesis_012 | 0.679 | 0.376 | 0.538 | 30.24 | 14.01 | -30.24 | -14.01 | 46.31 | 12.78 | 1.590 | 0.333 |
| thesis_013 | 0.036 | 0.036 | 0.000 | 0.03 | 3.60 | 0.03 | -3.60 | 6.28 | 1.09 | 0.678 | 0.704 |
| thesis_014 | 0.526 | 0.669 | 0.294 | 14.37 | 23.17 | 14.37 | -23.17 | 7.16 | 5.87 | 1.448 | 0.737 |
| thesis_015 | 0.417 | 0.351 | 0.500 | 6.58 | 8.33 | -6.58 | 8.33 | 17.22 | 11.82 | 1.927 | 0.582 |
| thesis_017 | 0.743 | 0.872 | 0.650 | 12.90 | 9.34 | 12.90 | -9.34 | 5.16 | 1.25 | 1.172 | 0.775 |
| thesis_019 | 0.823 | 0.716 | 0.750 | 10.72 | 7.33 | -10.72 | -7.33 | 15.32 | 2.76 | 0.954 | 0.788 |
