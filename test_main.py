import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main as m


class PipelineTests(unittest.TestCase):
    def fixture(self, text="游戏闪退，进不去。"):
        return dict(topic="crash", sentiment="负向", intensity=2, impact=3,
                    summary="启动闪退", evidence=text, suggestion="复现并检查日志。", needs_review=False)

    def row(self, rid="1"):
        text = "游戏闪退，进不去。"
        return dict(review_id=rid, text=text, source="test", source_url="", created_at="2026-09-10T10:00:00+00:00")

    def test_reject_hallucinated_evidence(self):
        a = self.fixture(); a["evidence"] = "支付未到账"
        with self.assertRaises(ValueError): m.validate(a, "游戏闪退，进不去。")

    def test_reject_bool_score_and_positive_damage(self):
        a = self.fixture(); a["impact"] = True
        with self.assertRaises(ValueError): m.validate(a, a["evidence"])
        a = self.fixture(); a["sentiment"] = "正向"
        with self.assertRaises(ValueError): m.validate(a, a["evidence"])

    def test_priority_and_denominator(self):
        a, b = self.fixture(), self.fixture()
        a.update(intensity=1, impact=3)
        b.update(intensity=3, impact=1)
        rows = [dict(self.row(str(i)), status="ok", mode="api", result=r) for i,r in enumerate((a,b))]
        rows.append(dict(self.row("3"), status="error", mode="api"))
        rows.append(dict(self.row("4"), status="pending", mode="api"))
        _, topics, _, summary = m.aggregate(rows)
        # Sum of products = 6; product of the averages * n would incorrectly give 8.
        self.assertEqual(topics[0]["priority_score"], 6)
        self.assertEqual(topics[0]["impact3_count"], 1)
        self.assertEqual(summary["coverage"], .5)
        self.assertEqual(summary["negative_rate"], 1)

    def test_zero_success_is_not_zero_negative(self):
        _, topics, _, summary = m.aggregate([dict(self.row(), status="error", mode="api")])
        self.assertIsNone(summary["negative_rate"])
        self.assertEqual(topics, [])

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"reviews.json"
            m.dump(p, [self.row(), self.row()])
            with self.assertRaises(ValueError): m.load_rows(p)

    def test_formula_text_escaped(self):
        self.assertEqual(m.excel_safe("  =HYPERLINK(\"bad\")"), "'  =HYPERLINK(\"bad\")")
        self.assertEqual(m.excel_safe(3), 3)

    def test_json_retry(self):
        good = {"choices":[{"finish_reason":"stop", "message":{"content":json.dumps(self.fixture())}}]}
        bad = {"choices":[{"finish_reason":"stop", "message":{"content":"not JSON"}}]}
        with patch.object(m, "http_json", side_effect=[bad,good]) as call:
            result, _ = m.call_model(self.row(), "prompt", "model", "https://example.com", "fake")
            self.assertEqual(call.call_count, 2)
            self.assertEqual(result["topic"], "crash")

    def test_resume_and_config_change(self):
        with tempfile.TemporaryDirectory() as d:
            path, out = Path(d)/"input.json", Path(d)/"run"
            m.dump(path, [self.row()])
            args = argparse.Namespace(input=path, out=out, limit=10)
            env = dict(LLM_API_KEY="fake", LLM_MODEL="test", LLM_BASE_URL="https://example.com")
            with patch.dict(os.environ, env), patch.object(m, "call_model", return_value=(self.fixture(),{})) as call:
                m.analyze(args); m.analyze(args)
                self.assertEqual(call.call_count, 1)
                changed = self.row(); changed["text"] = "新评论"
                m.dump(path,[changed])
                with self.assertRaises(ValueError): m.analyze(args)

    def test_steam_cursor_and_id_dedup(self):
        item = dict(recommendationid="11", review="测试", timestamp_created=1700000000, voted_up=False)
        another = dict(item, recommendationid="12")
        pages = [dict(success=1, reviews=[item], cursor="cursor + / ="),
                 dict(success=1, reviews=[item,another], cursor="end")]
        with tempfile.TemporaryDirectory() as d, patch.object(m,"http_json",side_effect=pages) as call, patch.object(m.time,"sleep"):
            args = argparse.Namespace(appid=1, limit=2, language="schinese", max_pages=3, out=Path(d)/"data.json")
            m.fetch(args)
            self.assertEqual(len(m.read_json(args.out)),2)
            self.assertIn("cursor=cursor+%2B+%2F+%3D",call.call_args.args[0])

    def test_appstore_fetch_omits_reviewer_identity(self):
        entry = {"author":{"name":{"label":"do-not-save"},"uri":{"label":"private-profile"}},
                 "updated":{"label":"2026-09-12T07:49:30-07:00"},"im:rating":{"label":"1"},
                 "im:version":{"label":"1.0"},"id":{"label":"99"},"title":{"label":"标题"},
                 "content":{"label":"正文"},"link":{"attributes":{"href":"https://example.com/review"}}}
        with tempfile.TemporaryDirectory() as d, patch.object(m,"http_json",return_value={"feed":{"entry":[entry]}}), patch.object(m.time,"sleep"):
            args=argparse.Namespace(appid=1544884479,store="cn",limit=2,delay=0,sort="mostRecent",out=Path(d)/"reviews.json")
            m.fetch_appstore(args)
            rows=m.read_json(args.out)
            self.assertEqual(rows[0]["text"],"标题\n正文")
            self.assertNotIn("author",rows[0])
            self.assertNotIn("device",rows[0])

    def test_prepare_has_no_overlap(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); rows=[self.row(str(i)) for i in range(12)]; m.dump(root/"in.json",rows)
            args=argparse.Namespace(input=root/"in.json",eval_size=5,dev_size=4,seed=42,out=root/"split")
            m.prepare(args)
            dev={r["review_id"] for r in m.read_json(root/"split/dev_sample.json")}
            import csv
            with (root/"split/blind_labels.csv").open(encoding="utf-8-sig",newline="") as f:
                blind={r["review_id"] for r in csv.DictReader(f)}
            self.assertFalse(dev & blind)

    def test_matchmaking_maps_to_numeric_balance(self):
        self.assertEqual(m.TOPICS["matchmaking"], ("数值平衡", "匹配公平性"))

    def test_evaluation_coverage_and_incomplete_labels(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            rows=[dict(self.row("1"),status="ok",mode="api",result=self.fixture()),
                  dict(self.row("2"),status="error",mode="api")]
            m.dump(root/"results.json",rows)
            labels=[dict(review_id=str(i),text=self.row()["text"],gold_category="BUG",gold_sentiment="负向") for i in (1,2)]
            fields=["review_id","text","gold_category","gold_sentiment"]
            m.write_csv(root/"labels.csv",labels,fields)
            args=argparse.Namespace(input=root/"results.json",labels=root/"labels.csv",out=root/"eval.json")
            m.evaluate(args)
            score=m.read_json(args.out)
            self.assertEqual(score["category_accuracy"],1)
            self.assertEqual(score["coverage"],.5)
            self.assertEqual(score["end_to_end_category_success"],.5)
            labels[0]["gold_category"]=""
            m.write_csv(root/"labels.csv",labels,fields)
            with self.assertRaises(ValueError): m.evaluate(args)

    def test_qa_sample_contains_blank_human_fields(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            row=dict(self.row(),status="ok",mode="api",result=self.fixture(),usage={"total_tokens":9})
            m.dump(root/"results.json",[row])
            args=argparse.Namespace(input=root/"results.json",limit=1,seed=1,out=root/"qa.csv")
            m.qa_sample(args)
            import csv
            with (root/"qa.csv").open(encoding="utf-8-sig",newline="") as f:
                saved=list(csv.DictReader(f))
            self.assertEqual(saved[0]["predicted_category"],"BUG")
            self.assertEqual(saved[0]["category_correct"],"")


if __name__ == "__main__":
    unittest.main()
