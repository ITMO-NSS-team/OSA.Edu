from __future__ import annotations

import unittest

from backend.app.literature.extractor import (
    _indent_positioned_lines,
    _select_page_text,
    extract_references,
)
from backend.app.literature.metadata_compare import (
    author_overlap,
    deterministic_decision,
)
from backend.app.literature.normalize_references import normalize_text
from backend.app.literature.web_verifier import (
    _assert_web_search_performed,
    normalize_web_decision,
)


class LiteratureExtractionRegressionTests(unittest.TestCase):
    def test_singular_reference_heading_is_supported(self) -> None:
        text = """Conclusion
Reference
(1) Ma, Z.; Sun, S. Magnetic Nanoparticles. Chem. Rev. 2023.
(2) Chen, X.; Liu, Y. Unconventional Magnons. Nature 2025.
Data Availability
Repository details follow.
"""

        mode, bibliography = extract_references(text)
        rows = normalize_text(bibliography)

        self.assertEqual("heading", mode)
        self.assertIn("Magnetic Nanoparticles", bibliography)
        self.assertIn("Unconventional Magnons", bibliography)
        self.assertNotIn("Data Availability", bibliography)
        self.assertEqual(2, len(rows))
        self.assertEqual("1", rows[0]["number"])
        self.assertEqual("2", rows[1]["number"])

    def test_native_order_wins_when_column_sort_corrupts_references_heading(self) -> None:
        sorted_text = "                 References                 likova, J.; Polev, K.\nCemri, M.; Pan, M. Z."
        native_text = "References\nCemri, M.; Pan, M. Z.\nZhidkovskaya, A.; Be-\nlikova, J.; Polev, K."

        selected, prefer_native_order = _select_page_text(sorted_text, native_text, False)

        self.assertTrue(prefer_native_order)
        self.assertEqual(native_text, selected)

    def test_native_layout_preserves_hanging_indents_in_both_columns(self) -> None:
        text = _indent_positioned_lines(
            [
                (70.9, "References"),
                (70.9, "First Author. 2024. First paper."),
                (81.8, "continued on the left."),
                (317.1, "continued at the top of the right column."),
                (306.1, "Second Author. 2025. Second paper."),
                (317.1, "continued on the right."),
            ],
            595.0,
        )

        self.assertEqual(
            [
                "References",
                "First Author. 2024. First paper.",
                " continued on the left.",
                " continued at the top of the right column.",
                "Second Author. 2025. Second paper.",
                " continued on the right.",
            ],
            text.splitlines(),
        )

    def test_appendix_heading_after_references_is_clipped(self) -> None:
        text = """Introduction
References
Dayu Yang, Antoine Simoulin, Xin Qian, Xiaoyi Liu,
Yuwei Cao, Zhaopu Teng, and Grey Yang. 2025.
Docagent:
A multi-agent system for automated
code documentation generation.
arXiv preprint arXiv:2504.08725.
7
A
Claim Extraction Prompts
This appendix summarizes the prompt families
"""

        mode, bibliography = extract_references(text)

        self.assertEqual("heading", mode)
        self.assertIn("A multi-agent system for automated", bibliography)
        self.assertNotIn("A Claim Extraction Prompts", bibliography)
        self.assertNotIn("This appendix summarizes", bibliography)

    def test_split_appendix_heading_ends_bibliography(self) -> None:
        text = """References
First Author. 2024. First paper.
 continued line.
A
Run Registry
All experiment rows are listed here.
"""

        mode, bibliography = extract_references(text)

        self.assertEqual("heading", mode)
        self.assertIn("First Author", bibliography)
        self.assertNotIn("Run Registry", bibliography)
        self.assertNotIn("experiment rows", bibliography)

    def test_wrapped_reference_title_is_not_treated_as_tail_heading(self) -> None:
        text = """References
Dayu Yang, Antoine Simoulin, Xin Qian, Xiaoyi Liu,
Yuwei Cao, Zhaopu Teng, and Grey Yang. 2025.
Docagent:
A multi-agent system for automated
code documentation generation.
arXiv preprint arXiv:2504.08725.
"""

        mode, bibliography = extract_references(text)

        self.assertEqual("heading", mode)
        self.assertIn("Docagent:", bibliography)
        self.assertIn("A multi-agent system for automated", bibliography)
        self.assertIn("code documentation generation", bibliography)

    def test_decimal_metric_is_not_a_numbered_reference(self) -> None:
        text = """References
First Author. 2024. First paper.
 continuation line.
0.807 after reranking.
"""

        rows = normalize_text(text)

        self.assertEqual(2, len(rows))
        self.assertTrue(rows[0]["reference"].startswith("First Author"))
        self.assertTrue(rows[1]["reference"].startswith("0.807"))

    def test_author_year_references_without_hanging_indent_stay_whole(self) -> None:
        text = """References
Bloor, M.; Torraca, J.; Sandoval, I. O.; Ahmed, A.; White,
M.; Mercangöz, M.; and Mowbray, M. 2024. PC-Gym:
Benchmark Environments for Process Control Problems.
Lee, H.; Choi, D.; Kim, B.; Park, H.; and Kim, S. G.
2026. NRT-Bench: Benchmarking Multi-Turn Red-Teaming.
"""

        rows = normalize_text(text)

        self.assertEqual(2, len(rows))
        self.assertIn("White, M.; Mercangöz", rows[0]["reference"])
        self.assertIn("2026. NRT-Bench", rows[1]["reference"])

    def test_full_name_start_with_middle_initial_splits_reference(self) -> None:
        text = """References
Feng Wang, Yuqing Li, and Han Xiao. Jina-reranker-v3: Last but not late interaction for listwise
document reranking. arXiv preprint arXiv:2509.25085, 2025.
Geemi P Wellawatte, Huixuan Guo, Magdalena Lederbauer, Anna Borisova, Matthew Hart, Marta
Brucka, and Philippe Schwaller. Chemlit-qa: a human evaluated dataset for chemistry rag tasks.
Machine Learning: Science and Technology, 6(2):020601, 2025.
"""

        rows = normalize_text(text)

        self.assertEqual(2, len(rows))
        self.assertTrue(rows[0]["reference"].startswith("Feng Wang"))
        self.assertNotIn("Geemi P Wellawatte", rows[0]["reference"])
        self.assertTrue(rows[1]["reference"].startswith("Geemi P Wellawatte"))
        self.assertIn("Marta Brucka", rows[1]["reference"])

    def test_line_numbered_multiline_references_stay_whole(self) -> None:
        text = """References
474    Marah Abdin, Sam Ade Jacobs, Ammar Ahmad Awan, Jyoti Aneja, Awais Behnan, Arash
475      Behnam, et al. Phi-3 technical report: A highly capable language model locally on your
476       phone, 2024. URL https://arxiv.org/abs/2404.14219.
      AI Forever Research. LIBRA: A benchmark for Russian long-context language understand-
           ing. https://huggingface.co/datasets/ai-forever/LIBRA, 2025.
"""

        rows = normalize_text(text)

        self.assertEqual(2, len(rows))
        self.assertIn("Arash Behnam, et al.", rows[0]["reference"])
        self.assertIn("locally on your phone", rows[0]["reference"])
        self.assertNotIn("LIBRA", rows[0]["reference"])
        self.assertTrue(rows[1]["reference"].startswith("AI Forever Research. LIBRA"))
        self.assertIn("long-context language understanding", rows[1]["reference"])

    def test_split_arxiv_url_continuation_stays_with_reference(self) -> None:
        text = """References
584     Jared Kaplan, Sam McCandlish, Tom Henighan, Tom B. Brown, Benjamin Chess, Rewon
          Child, Scott Gray, Alec Radford, Jeffrey Wu, and Dario Amodei. Scaling laws for neural585
         language models. arXiv preprint arXiv:2001.08361, 2020. URL https://arxiv.org/abs/
586
          2001.08361.
"""

        rows = normalize_text(text)

        self.assertEqual(1, len(rows))
        self.assertIn("Scaling laws for neural language models", rows[0]["reference"])
        self.assertIn("https://arxiv.org/abs/2001.08361", rows[0]["reference"])

    def test_arxiv_id_with_trailing_line_number_splits_before_next_source(self) -> None:
        text = """References
627    David Patterson, Joseph Gonzalez, Quoc Le, Chen Liang, Lluis-Miquel Munguia, Daniel
628       Rothchild, David So, Maud Texier, and Jeff Dean. Carbon emissions and large neural
629      network training. arXiv preprint arXiv:2104.10350, 2021. URL https://arxiv.org/abs/
          2104.10350.630
        Plato. Plato: Protagoras and Meno. Penguin Classics, 1956. Translated with an introduction
         by W. K. C. Guthrie.
"""

        rows = normalize_text(text)

        self.assertEqual(2, len(rows))
        self.assertIn("https://arxiv.org/abs/2104.10350", rows[0]["reference"])
        self.assertTrue(rows[1]["reference"].startswith("Plato. Plato"))
        self.assertNotIn("2104.10350", rows[1]["reference"])

    def test_trailing_line_number_after_year_splits_before_next_source(self) -> None:
        text = """References
678    Yubo Wang, Xuezhi Ma, Zhaowei Zhang, et al. MMLU-Pro: A more robust and challeng-
679       ing multi-task language understanding benchmark. In Advances in Neural Information
          Processing Systems, volume 37, 2024.680
681    Jason Wei, Xuezhi Wang, Dale Schuurmans, Maarten Bosma, Brian Ichter, Fei Xia, Ed Chi,
682     Quoc V Le, and Denny Zhou. Chain-of-thought prompting elicits reasoning in large
683       language models. In S. Koyejo, S. Mohamed, A. Agarwal, D. Belgrave, K. Cho, and
684      A. Oh (eds.), Advances in Neural Information Processing Systems, volume 35, pp. 24824–
685       24837. Curran Associates, Inc., 2022.
       Zhishang Xiang, Chuanjie Wu, Qinggang Zhang, Shengyuan Chen, Zijin Hong, Xiao Huang,
        and Jinsong Su. When to use graphs in RAG: A comprehensive analysis for graph retrieval-
         augmented generation. In International Conference on Learning Representations (ICLR
689       2026), 2026. URL https://openreview.net/pdf?id=i9q9xDMjG7.
"""

        rows = normalize_text(text)

        self.assertEqual(3, len(rows))
        self.assertTrue(rows[0]["reference"].startswith("Yubo Wang"))
        self.assertNotIn("Jason Wei", rows[0]["reference"])
        self.assertTrue(rows[1]["reference"].startswith("Jason Wei"))
        self.assertIn("Chain-of-thought prompting", rows[1]["reference"])
        self.assertTrue(rows[2]["reference"].startswith("Zhishang Xiang"))

    def test_under_review_footer_does_not_merge_adjacent_references(self) -> None:
        text = """References
485     Sher Badshah, Ali Emami, and Hassan Sajjad. Judge, retrieve, or abstain: Uncertainty-guarded LLM
          judging with provable risk guarantees. In Conference on Language Modeling (COLM), 2026.


                                              9
       Under review as a conference paper at ICLR 2027


486
         Julia Belikova, Rauf Parchiev, Evgeny Egorov, Grigorii Davydenko, Gleb Gusev, Andrey
487       Savchenko, and Maksim Makarenko. Managing procedural memory in LLM agents: Control,
488        adaptation, and evaluation. arXiv preprint arXiv:2606.23127, 2026.
"""

        rows = normalize_text(text)

        self.assertEqual(2, len(rows))
        self.assertTrue(rows[0]["reference"].startswith("Sher Badshah"))
        self.assertNotIn("Under review", rows[0]["reference"])
        self.assertNotIn("Julia Belikova", rows[0]["reference"])
        self.assertTrue(rows[1]["reference"].startswith("Julia Belikova"))
        self.assertIn("arXiv:2606.23127", rows[1]["reference"])

    def test_title_with_comma_is_not_mistaken_for_an_author(self) -> None:
        text = """References
Zheng, Y.; Mao, S.; Zhang, D.; and Cai, W. 2025. Reflex
First, Reflect Later: Latency-Aware Embodied LLM Agents
for Dynamic Response. arXiv preprint arXiv:2506.07223.
"""

        rows = normalize_text(text)

        self.assertEqual(1, len(rows))
        self.assertIn("Reflex First, Reflect Later", rows[0]["reference"])

    def test_split_access_date_does_not_start_numbered_mode(self) -> None:
        text = """References
Eli Salamie. 2025. Readme file generator, powered
by ai. https://github.com/eli64s/README-AI.
Accessed:
2025-09-
15.
Qinyu Luo, Yining Ye, Shihao Liang, Zhong Zhang,
Yujia Qin, Yaxi Lu, Yesai Wu, Xin Cong, Yankai
Lin, Yingli Zhang, et al. 2024.
Repoagent: An
llm-powered open-source framework for repository-
level code documentation generation. arXiv preprint
arXiv:2402.16667.
"""

        rows = normalize_text(text)

        self.assertEqual(2, len(rows))
        self.assertEqual("1", rows[0]["number"])
        self.assertEqual("2", rows[1]["number"])
        self.assertIn("Readme file generator", rows[0]["reference"])
        self.assertIn("Repoagent", rows[1]["reference"])

    def test_current_paper_unnumbered_references_split_into_records(self) -> None:
        text = """References
Fabien CY Benureau and Nicolas P Rougier. 2018. Re-
run, repeat, reproduce, reuse, replicate: transforming
code into scientific contributions. Frontiers in neu-
roinformatics, 11:69.
Eli Salamie. 2025. Readme file generator, powered
by ai. https://github.com/eli64s/README-AI.
Accessed: 2025-09-15.
Aleksandra Eliseeva, Alexander Kovrigin, Ilia Kholkin,
Egor Bogomolov, and Yaroslav Zharov. 2025. En-
vbench: A benchmark for automated environment
setup. arXiv preprint arXiv:2503.14443.
Michael Färber. 2020. Analyzing the github repositories
of research papers. In Proceedings of the ACM/IEEE
joint conference on digital libraries in 2020, pages
491–492.
Peter Ivie and Douglas Thain. 2018. Reproducibility
in scientific computing. ACM Computing Surveys
(CSUR), 51(3):1–36.
Yuta Koreeda, Terufumi Morishita, Osamu Imaichi, and
Yasuhiro Sogawa. 2023.
Larch: Large language
model-based automatic readme creation with heuris-
tics. In Proceedings of the 32nd ACM International
Conference on Information and Knowledge Manage-
ment, pages 5066–5070.
Linus
Unnebäck.
2025.
Api
documentation
generator.
https://github.com/LinusU/
ts-readme-generator.
Accessed:
2025-09-
15.
Qinyu Luo, Yining Ye, Shihao Liang, Zhong Zhang,
Yujia Qin, Yaxi Lu, Yesai Wu, Xin Cong, Yankai
Lin, Yingli Zhang, et al. 2024.
Repoagent: An
llm-powered open-source framework for repository-
level code documentation generation. arXiv preprint
arXiv:2402.16667.
K. Jarrod Millman, Matthew Brett, Ross Barnowski,
and Jean-Baptiste Poline. 2018. Teaching computa-
tional reproducibility for neuroimaging. Frontiers in
Neuroscience, 12:727.
OpenBMB. 2024.
An llm-powered framework for
repository-level code documentation generation.
https://github.com/OpenBMB/RepoAgent.
Ac-
cessed: 2025-09-15.
Harald Semmelrock, Simone Kopeinik, Dieter Theiler,
Tony Ross-Hellauer, and Dominik Kowald. 2023. Re-
producibility in machine learning-driven research.
arXiv preprint arXiv:2307.10320.
Simon
Kenyon
Shepard.
2021.
mer-
maidjs
diagrams
generator.
https:
//github.com/SimonKenyonShepard/
mermaidjs-github-svg-generator.
Accessed:
2025-09-15.
Xiangru Tang, Yuliang Liu, Zefan Cai, Yanjun Shao,
Junjie Lu, Yichi Zhang, Zexuan Deng, Helan Hu,
Kaikai An, Ruijun Huang, et al. 2023. Ml-bench:
Evaluating large language models and agents for ma-
chine learning tasks on repository-level code. arXiv
preprint arXiv:2311.09835.
Harold Thimbleby. 2024. Improving science that uses
code. The Computer Journal, 67(4):1381–1404.
Ana Trisovic, Matthew K Lau, Thomas Pasquier, and
Mercè Crosas. 2022. A large-scale study on research
code quality and execution. Scientific Data, 9(1):60.
Dayu Yang, Antoine Simoulin, Xin Qian, Xiaoyi Liu,
Yuwei Cao, Zhaopu Teng, and Grey Yang. 2025.
Docagent:
A multi-agent system for automated
code documentation generation.
arXiv preprint
arXiv:2504.08725.
7
"""

        rows = normalize_text(text)
        refs = [str(row["reference"]) for row in rows]

        self.assertEqual(16, len(rows))
        self.assertTrue(refs[6].startswith("Linus Unnebäck. 2025"))
        self.assertTrue(refs[10].startswith("Harald Semmelrock"))
        self.assertTrue(refs[11].startswith("Simon Kenyon Shepard. 2021"))
        self.assertTrue(refs[15].startswith("Dayu Yang"))
        self.assertIn("OpenBMB. 2024", refs[9])
        self.assertIn("A multi-agent system for automated", refs[15])


class LiteratureMetadataRegressionTests(unittest.TestCase):
    def test_full_name_author_list_with_middle_initials_matches_arxiv_candidate(self) -> None:
        reference = (
            "Jared Kaplan, Sam McCandlish, Tom Henighan, Tom B. Brown, "
            "Benjamin Chess, Rewon Child, Scott Gray, Alec Radford, Jeffrey Wu, "
            "and Dario Amodei. Scaling laws for neural language models. "
            "arXiv preprint arXiv:2001.08361, 2020. "
            "URL https://arxiv.org/abs/2001.08361."
        )
        cited_title = "Scaling laws for neural language models"
        authors = (
            "Jared Kaplan; Sam McCandlish; Tom Henighan; Tom B. Brown; "
            "Benjamin Chess; Rewon Child; Scott Gray; Alec Radford; "
            "Jeffrey Wu; Dario Amodei"
        )

        self.assertEqual(1.0, author_overlap(reference, cited_title, authors))

        row = {
            "reference": reference,
            "cited_title": cited_title,
            "source_type": "PREPRINT",
            "candidates": [
                {
                    "title": "Scaling Laws for Neural Language Models",
                    "authors": authors,
                    "year": "2020",
                    "arxiv_id": "2001.08361",
                    "url": "https://arxiv.org/abs/2001.08361",
                }
            ],
        }

        decision = deterministic_decision(row)

        self.assertIsNotNone(decision)
        self.assertEqual("OK", decision["status"])


class LiteratureWebRegressionTests(unittest.TestCase):
    def test_cp1251_mojibake_is_repaired(self) -> None:
        decision = normalize_web_decision(
            {"notes": "Р–РёРІРѕР№ РІРµР±-РїРѕРёСЃРє РЅРµ РІС‹РїРѕР»РЅРµРЅ."}
        )

        self.assertEqual("Живой веб-поиск не выполнен.", decision["notes"])

    def test_unavailable_browser_is_not_accepted_as_a_web_check(self) -> None:
        decision = normalize_web_decision(
            {"notes": "Живой веб-поиск не выполнен: браузеры недоступны."}
        )

        with self.assertRaisesRegex(RuntimeError, "не выполнил"):
            _assert_web_search_performed(decision)


if __name__ == "__main__":
    unittest.main()
