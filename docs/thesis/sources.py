"""Довідник джерел. Дані наведено за пам'яттю автора роботи і підлягають звірці з першоджерелами
(DOI, сторінки, роки) перед здачею. Для онлайн-ресурсів дату звернення потрібно проставити вручну."""

SOURCES = {
    "nist_rmf": "Artificial Intelligence Risk Management Framework (AI RMF 1.0): NIST AI 100-1. Gaithersburg: "
                "National Institute of Standards and Technology, 2023. 48 p. DOI: 10.6028/NIST.AI.100-1.",
    "nist_genai": "Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile: "
                  "NIST AI 600-1. Gaithersburg: National Institute of Standards and Technology, 2024. "
                  "DOI: 10.6028/NIST.AI.600-1.",
    "eliza": "Weizenbaum J. ELIZA – a computer program for the study of natural language communication between "
             "man and machine. Communications of the ACM. 1966. Vol. 9, No. 1. P. 36–45.",
    "chatbots": "Adamopoulou E., Moussiades L. Chatbots: History, technology, and applications. "
                "Machine Learning with Applications. 2020. Vol. 2. Art. 100006. DOI: 10.1016/j.mlwa.2020.100006.",
    "bm25": "Robertson S., Zaragoza H. The probabilistic relevance framework: BM25 and beyond. "
            "Foundations and Trends in Information Retrieval. 2009. Vol. 3, No. 4. P. 333–389. "
            "DOI: 10.1561/1500000019.",
    "dpr": "Karpukhin V. et al. Dense passage retrieval for open-domain question answering. "
           "Proceedings of EMNLP 2020. 2020. P. 6769–6781. DOI: 10.18653/v1/2020.emnlp-main.550.",
    "rag": "Lewis P. et al. Retrieval-augmented generation for knowledge-intensive NLP tasks. "
           "Advances in Neural Information Processing Systems 33 (NeurIPS 2020). 2020. P. 9459–9474.",
    "rag_survey": "Gao Y. et al. Retrieval-augmented generation for large language models: a survey. "
                  "arXiv:2312.10997. 2023. URL: https://arxiv.org/abs/2312.10997.",
    "halluc": "Ji Z. et al. Survey of hallucination in natural language generation. "
              "ACM Computing Surveys. 2023. Vol. 55, No. 12. Art. 248. DOI: 10.1145/3571730.",
    "rag_halluc": "Shuster K. et al. Retrieval augmentation reduces hallucination in conversation. "
                  "Findings of EMNLP 2021. 2021. P. 3784–3803. DOI: 10.18653/v1/2021.findings-emnlp.320.",
    "transformer": "Vaswani A. et al. Attention is all you need. Advances in Neural Information Processing "
                   "Systems 30 (NIPS 2017). 2017. P. 5998–6008.",
    "gpt3": "Brown T. B. et al. Language models are few-shot learners. Advances in Neural Information "
            "Processing Systems 33 (NeurIPS 2020). 2020. P. 1877–1901.",
    "instructgpt": "Ouyang L. et al. Training language models to follow instructions with human feedback. "
                   "Advances in Neural Information Processing Systems 35 (NeurIPS 2022). 2022. P. 27730–27744.",
    "llm_survey": "Zhao W. X. et al. A survey of large language models. arXiv:2303.18223. 2023. "
                  "URL: https://arxiv.org/abs/2303.18223.",
    "zeroshot": "Kojima T. et al. Large language models are zero-shot reasoners. Advances in Neural Information "
                "Processing Systems 35 (NeurIPS 2022). 2022. P. 22199–22213.",
    "cot": "Wei J. et al. Chain-of-thought prompting elicits reasoning in large language models. "
           "Advances in Neural Information Processing Systems 35 (NeurIPS 2022). 2022. P. 24824–24837.",
    "prompt_survey": "Sahoo P. et al. A systematic survey of prompt engineering in large language models: "
                     "techniques and applications. arXiv:2402.07927. 2024. URL: https://arxiv.org/abs/2402.07927.",
    "bleu": "Papineni K. et al. BLEU: a method for automatic evaluation of machine translation. "
            "Proceedings of ACL 2002. 2002. P. 311–318.",
    "rouge": "Lin C.-Y. ROUGE: a package for automatic evaluation of summaries. Text Summarization Branches Out: "
             "Proceedings of the ACL-04 Workshop. 2004. P. 74–81.",
    "bertscore": "Zhang T. et al. BERTScore: evaluating text generation with BERT. International Conference "
                 "on Learning Representations (ICLR 2020). 2020. arXiv:1904.09675.",
    "judge": "Zheng L. et al. Judging LLM-as-a-judge with MT-Bench and Chatbot Arena. Advances in Neural "
             "Information Processing Systems 36 (NeurIPS 2023), Datasets and Benchmarks Track. 2023. "
             "arXiv:2306.05685.",
    "geval": "Liu Y. et al. G-Eval: NLG evaluation using GPT-4 with better human alignment. "
             "Proceedings of EMNLP 2023. 2023. P. 2511–2522. DOI: 10.18653/v1/2023.emnlp-main.153.",
    "judge_bias": "Wang P. et al. Large language models are not fair evaluators. Proceedings of ACL 2024. 2024. "
                  "arXiv:2305.17926.",
    "helm": "Liang P. et al. Holistic evaluation of language models. Transactions on Machine Learning Research. "
            "2023. arXiv:2211.09110.",
    "arena": "Chiang W.-L. et al. Chatbot Arena: an open platform for evaluating LLMs by human preference. "
             "Proceedings of the 41st International Conference on Machine Learning (ICML 2024). 2024. "
             "arXiv:2403.04132.",
    "ragas": "Es S. et al. RAGAS: automated evaluation of retrieval augmented generation. Proceedings of the "
             "18th Conference of the EACL: System Demonstrations. 2024. P. 150–158. arXiv:2309.15217.",
    "frugalgpt": "Chen L., Zaharia M., Zou J. FrugalGPT: how to use large language models while reducing cost "
                 "and improving performance. Transactions on Machine Learning Research. 2024. arXiv:2305.05176.",
    "kappa": "Cohen J. A coefficient of agreement for nominal scales. Educational and Psychological "
             "Measurement. 1960. Vol. 20, No. 1. P. 37–46.",
    "wilcoxon": "Wilcoxon F. Individual comparisons by ranking methods. Biometrics Bulletin. 1945. "
                "Vol. 1, No. 6. P. 80–83.",
    "friedman": "Friedman M. The use of ranks to avoid the assumption of normality implicit in the analysis "
                "of variance. Journal of the American Statistical Association. 1937. Vol. 32, No. 200. "
                "P. 675–701.",
    "holm": "Holm S. A simple sequentially rejective multiple test procedure. Scandinavian Journal of "
            "Statistics. 1979. Vol. 6, No. 2. P. 65–70.",
    "evals": "OpenAI Evals: a framework for evaluating LLMs and LLM systems. URL: "
             "https://github.com/openai/evals (дата звернення: [[__.11.2026]]).",
    "lm_harness": "Gao L. et al. Language Model Evaluation Harness. EleutherAI. URL: "
                  "https://github.com/EleutherAI/lm-evaluation-harness (дата звернення: [[__.11.2026]]).",
    "promptfoo": "Promptfoo: test your prompts, agents, and RAGs. URL: https://www.promptfoo.dev "
                 "(дата звернення: [[__.11.2026]]).",
    "deepeval": "DeepEval: the LLM evaluation framework. Confident AI. URL: "
                "https://github.com/confident-ai/deepeval (дата звернення: [[__.11.2026]]).",
    "kosiak_rag": "Косяк М. Архітектура автономних ШІ-агентів на базі RAG та оркестратора N8N для "
                  "автоматизації бізнес-процесів. Інформаційне суспільство: проблеми та перспективи: матер. "
                  "ХІ всеукр. наук.-практ. конф. Одеса: НУ «ОЮА», 22 травня 2026 р. С. 54–56. "
                  "DOI: 10.32837/11300.33389.",
    "geoquery": "Javaherian Pour E., Chen Y., Kesminas A., Rajabifard A., Ghasemi Tootkaboni M. An automated "
                "framework for natural language-based spatial query using large language models. "
                "Journal of Geovisualization and Spatial Analysis. 2026. Vol. 10. "
                "DOI: 10.1007/s41651-026-00260-9.",
}
