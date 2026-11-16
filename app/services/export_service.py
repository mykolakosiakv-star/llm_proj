"""
Модуль експорту результатів серійних експериментів у формати Markdown, CSV та LaTeX.
"""

from typing import Dict, Any
from app.config import EXPORTS_DIR
from app.services.analytics import compute_run_analytics


def export_run_tables_for_thesis(run_id: int) -> Dict[str, Any]:
    analytics = compute_run_analytics(run_id)
    if not analytics or not analytics.get("by_model"):
        return {"markdown": "", "latex": "", "csv_models": ""}

    by_model = analytics["by_model"]
    by_prompt = analytics["by_prompt"]
    by_category = analytics["by_category"]
    by_context_mode = analytics.get("by_context_mode", [])
    run_info = analytics["run"]

    md_lines = [
        f"# Результати експериментального дослідження (Серія #{run_id}: {run_info['title']})",
        (
            f"*Дата: {run_info['created_at']} | Статус: {run_info['status']} | "
            f"Датасет: v{run_info.get('dataset_version', '2.0')} ({run_info.get('dataset_hash', '')}) | "
            f"Версія коду: {run_info.get('code_version', '')} | Вибірка: n = {analytics['overall']['count']}*\n"
        ),
        "## Порівняльна оцінка ефективності моделей (LLM)",
        "| Модель (LLM) | n | Фактична повнота $FC$, % (95% ДІ) | Лексична близькість $SS$, % | Відповідність формату, % | Композит $Q_{det}$, % (95% ДІ) | Рубрика (1-5) | Галюцинації, % | Час (mean / P95), мс | Автоматизація, % | Вартість (\\$/1M токенів) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for m in by_model:
        fc_ci = m.get("fact_coverage_ci95", [0, 0])
        q_ci = m.get("efficiency_ci95", [0, 0])
        price_flag = "" if m.get("price_verified") else " (довідк.)"
        c_1m = m.get("cost_per_1m_usd", m.get("cost_per_1k_usd", 0.0))
        md_lines.append(
            f"| **{m['model_name']}** | {m['count']} | **{m['fact_coverage_pct']}%** [{fc_ci[0]}; {fc_ci[1]}] | "
            f"{m['semantic_similarity_pct']}% | {m['format_compliance_pct']}% | "
            f"**{m['integral_efficiency_pct']}%** [{q_ci[0]}; {q_ci[1]}] | {m['avg_nist_score_5']} | "
            f"{m.get('hallucination_rate_pct', 0.0)}% | {m['latency_mean_ms']} / {m['latency_p95_ms']} | "
            f"{m['automation_rate_pct']}% | \\${c_1m:.4f}{price_flag} |"
        )

    md_lines.extend([
        "\n## Порівняння стратегій формування промптів (P1-P5)",
        "| Код | Стратегія промпту | n | Фактична повнота $FC$, % (95% ДІ) | Валідний JSON-контракт, % | Композит $Q_{det}$, % | Точність ескалації, % | Recall ескалації, % | Час, мс | Токени (вх/вих) |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])
    for p in by_prompt:
        fc_ci = p.get("fact_coverage_ci95", [0, 0])
        md_lines.append(
            f"| **{p['prompt_code']}** | {p['prompt_name_uk']} | {p['count']} | "
            f"**{p['fact_coverage_pct']}%** [{fc_ci[0]}; {fc_ci[1]}] | {p.get('contract_ok_rate_pct', p['format_compliance_pct'])}% | "
            f"**{p['integral_efficiency_pct']}%** | {p['escalation_accuracy_pct']}% | "
            f"{p.get('escalation_recall_pct', 0.0)}% | {p['latency_mean_ms']} | "
            f"{p['avg_input_tokens']}/{p['avg_output_tokens']} |"
        )

    if len(by_context_mode) >= 1:
        md_lines.extend([
            "\n## Порівняння режимів подачі контексту (Context Modes)",
            "| Режим контексту | n | Фактична повнота $FC$, % | Композит $Q_{det}$, % | Галюцинації, % | Hit@1, % | Hit@3, % | Автоматизація, % |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ])
        for cm_item in by_context_mode:
            h1 = f"{cm_item['retrieval_hit_at_1_pct']}%" if cm_item.get("retrieval_hit_at_1_pct") is not None else "-"
            h3 = f"{cm_item['retrieval_hit_at_3_pct']}%" if cm_item.get("retrieval_hit_at_3_pct") is not None else "-"
            md_lines.append(
                f"| **{cm_item['context_mode_name_uk']}** | {cm_item['count']} | "
                f"**{cm_item['fact_coverage_pct']}%** | **{cm_item['integral_efficiency_pct']}%** | "
                f"{cm_item.get('hallucination_rate_pct', 0.0)}% | {h1} | {h3} | {cm_item['automation_rate_pct']}% |"
            )

    md_lines.extend([
        "\n## Показники ефективності автоматизації за категоріями запитів",
        "| Категорія запитів | n | Фактична повнота $FC$, % (95% ДІ) | Композит $Q_{det}$, % | Автоматизація (без ескалації), % | Точність шлюзу ескалації, % | Критичні пропуски FN, % |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])
    for c in by_category:
        fc_ci = c.get("fact_coverage_ci95", [0, 0])
        md_lines.append(
            f"| **{c['category_name_uk']}** | {c['count']} | **{c['fact_coverage_pct']}%** [{fc_ci[0]}; {fc_ci[1]}] | "
            f"{c['integral_efficiency_pct']}% | {c.get('automation_success_non_esc_pct', c['automation_rate_pct'])}% | "
            f"{c['escalation_accuracy_pct']}% | {c.get('critical_fn_rate_pct', 0.0)}% |"
        )

    md_lines.append("\n## Статистично обґрунтовані наукові висновки")
    for idx, f in enumerate(analytics.get("scientific_findings", []), start=1):
        md_lines.append(f"{idx}. **{f['title']}:** {f['text']}")

    markdown_text = "\n".join(md_lines)

    csv_lines = [
        "Модель;Клас;n;Фактична повнота FC (%);FC 95% ДІ Низ;FC 95% ДІ Верх;Лексична близькість SS (%);Відповідність формату (%);Композит Q_det (%);SD Q_det;Галюцинації (%);Час середній (мс);Час P95 (мс);Автоматизація (%);Точність ескалації (%);Вартість за 1M токенів ($);Ціна верифікована"
    ]
    for m in by_model:
        fc_ci = m.get("fact_coverage_ci95", [0, 0])
        c_1m = m.get("cost_per_1m_usd", m.get("cost_per_1k_usd", 0.0))
        csv_lines.append(
            f"{m['model_name']};{m['model_class_uk']};{m['count']};{m['fact_coverage_pct']};{fc_ci[0]};{fc_ci[1]};"
            f"{m['semantic_similarity_pct']};{m['format_compliance_pct']};{m['integral_efficiency_pct']};"
            f"{m['efficiency_std']};{m.get('hallucination_rate_pct', 0.0)};{m['latency_mean_ms']};{m['latency_p95_ms']};"
            f"{m['automation_rate_pct']};{m['escalation_accuracy_pct']};{c_1m:.4f};{m.get('price_verified', False)}"
        )
    csv_text = "\n".join(csv_lines)

    latex_lines = [
        "\\begin{table}[htbp]",
        "\\centering",
        "\\caption{Порівняльна ефективність великих мовних моделей для автоматизації відповідей}",
        "\\begin{tabular}{lccccccc}",
        "\\hline",
        "\\textbf{Модель LLM} & \\textbf{$n$} & \\textbf{$FC$, \\%} & \\textbf{$SS$, \\%} & \\textbf{$FMT$, \\%} & \\textbf{$Q_{det}$, \\%} & \\textbf{Час, мс} & \\textbf{$AR$, \\%} \\\\",
        "\\hline",
    ]
    for m in by_model:
        latex_lines.append(
            f"{m['model_name']} & {m['count']} & {m['fact_coverage_pct']} & "
            f"{m['semantic_similarity_pct']} & {m['format_compliance_pct']} & "
            f"{m['integral_efficiency_pct']} & {m['latency_mean_ms']} & {m['automation_rate_pct']} \\\\"
        )
    latex_lines.extend([
        "\\hline",
        "\\end{tabular}",
        "\\label{tab:llm_comparison}",
        "\\end{table}",
    ])
    latex_text = "\n".join(latex_lines)

    (EXPORTS_DIR / f"thesis_report_run_{run_id}.md").write_text(markdown_text, encoding="utf-8")
    (EXPORTS_DIR / f"models_comparison_run_{run_id}.csv").write_text(csv_text, encoding="utf-8-sig")
    (EXPORTS_DIR / f"article_table_run_{run_id}.tex").write_text(latex_text, encoding="utf-8")

    return {
        "markdown": markdown_text,
        "csv_models": csv_text,
        "latex": latex_text,
        "saved_files": [
            str(EXPORTS_DIR / f"thesis_report_run_{run_id}.md"),
            str(EXPORTS_DIR / f"models_comparison_run_{run_id}.csv"),
            str(EXPORTS_DIR / f"article_table_run_{run_id}.tex"),
        ],
    }
