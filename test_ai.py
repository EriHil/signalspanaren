import unittest
from unittest import mock

import ai
import flaggor
import signaldb


class AnalysisDataTests(unittest.TestCase):
    def test_histogram_and_envelope_include_units_and_axes(self):
        text = ai.analysdata({
            "hist": ([0, 10, 20, 30], [0, 5, 10, 5]),
            "kurva": [0.25, 0.5, 1.0, 0.75],
            "kurva_s": 0.4,
        })
        self.assertIn("Hz:antal per bin normaliserat till max 1", text)
        self.assertIn("tid i sekunder från äldst till nyast", text)
        self.assertIn("0.40 s", text)
        self.assertIn("0.00:0.25", text)

    def test_analysis_data_is_sampled_with_both_endpoints(self):
        text = ai.analysdata({
            "hist": (list(range(120)), list(range(1, 121))),
            "kurva": [i / 399 for i in range(400)],
            "kurva_s": 1.0,
        })
        self.assertIn("32 punkter", text)
        self.assertIn("119:1.00", text)
        self.assertIn("40 punkter", text)
        self.assertIn("1.00:1.00", text)

    def test_chat_prompt_requires_grounding_uncertainty_and_conciseness(self):
        prompt = ai.systemprompt("SNR: 18.4 dB")
        self.assertIn("hitta inte på mätvärden", prompt)
        self.assertIn("högst 80 ord", prompt)
        self.assertIn("flagga eller databaskandidat bevisar aldrig", prompt)
        self.assertIn("identiteten inte kan bekräftas", prompt)
        self.assertIn("uttryckligen har bekräftat", prompt)

    def test_suggestion_requires_expected_string_fields_and_is_bounded(self):
        self.assertIsNone(ai.forslag_ur('FÖRSLAG={"namn":"VOLMET"}'))
        self.assertIsNone(ai.forslag_ur('FÖRSLAG={"namn":["VOLMET"],"typ":"Flyg","not":""}'))
        result = ai.forslag_ur(
            'FÖRSLAG={"namn":"' + "N" * 100 + '","typ":"Flyg","not":"' + "x" * 300 + '"}')
        self.assertEqual(len(result["namn"]), 60)
        self.assertEqual(len(result["not"]), 200)


class AISearchTests(unittest.TestCase):
    def test_modulation_query_returns_signal_types(self):
        result = ai.sok_modulation("FSK", signaldb.SIGNALER)
        self.assertIsNotNone(result)
        self.assertTrue(result)
        self.assertTrue(all({"FSK2", "FSK4", "MFSK", "MSK"}.intersection(x["mod"]) for x in result))
        cw = ai.sok_modulation("CW", signaldb.SIGNALER)
        self.assertIsNotNone(cw)
        self.assertTrue(all("OOK" in x["mod"] for x in cw))
        self.assertIsNone(ai.sok_modulation("VOLMET", signaldb.SIGNALER))

    def test_direct_service_and_catalog_category_find_flag_candidates(self):
        direct = [{"f": 5_505_000, "namn": "Shannon VOLMET", "typ": "volmet", "lage": "USB"}]
        result = ai.sok_flaggkandidater("VOLMET", direct, signaldb.SIGNALER, flaggor.typ_for)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["namn"], "Shannon VOLMET")

        noted = [{"f": 5_505_000, "namn": "HF signal", "typ": "Övrigt", "not": "VOLMET weather"}]
        result = ai.sok_flaggkandidater("VOLMET", noted, signaldb.SIGNALER, flaggor.typ_for)
        self.assertEqual(result[0]["namn"], "HF signal")

        military = [{"f": 6_429_000, "namn": "MILCOM station", "typ": "milcom", "lage": "USB"}]
        result = ai.sok_flaggkandidater("STANAG 4285", military, signaldb.SIGNALER, flaggor.typ_for)
        self.assertEqual([x["namn"] for x in result], ["MILCOM station"])

    def test_search_response_is_limited_to_candidate_ids(self):
        candidates = [
            {"id": "F001", "f": 1, "namn": "first"},
            {"id": "F002", "f": 2, "namn": "second"},
        ]
        result = ai.sok_tolka(
            '{"results":["F002",{"id":"F001","reason":"untrusted text"},"F999","F002"]}',
            candidates,
        )
        self.assertEqual([x["id"] for x in result], ["F002"])

    def test_invalid_search_response_raises(self):
        with self.assertRaises(ValueError):
            ai.sok_tolka("no JSON", [])


class DeepSeekTests(unittest.TestCase):
    def test_deepseek_chat_uses_selected_model_and_bearer_key(self):
        response = {"choices": [{"message": {"content": "Analys"}}]}
        cfg = {
            "aktiv": True,
            "backend": "deepseek",
            "deepseek_modell": "deepseek-flash",
            "deepseek_api_key": "test-key",
        }
        with mock.patch.object(ai, "_post", return_value=response) as post:
            result = ai.svar(cfg, [{"role": "user", "content": "Analysera signalen"}], "system")

        self.assertEqual(result, "Analys")
        args, _kwargs = post.call_args
        self.assertEqual(args[0], ai.DEEPSEEK_URL)
        self.assertEqual(args[1]["model"], "deepseek-flash")
        self.assertEqual(args[2]["Authorization"], "Bearer test-key")

    def test_deepseek_requires_api_key_without_network_request(self):
        cfg = {"aktiv": True, "backend": "deepseek", "deepseek_api_key": ""}
        with mock.patch.object(ai, "_post") as post:
            with self.assertRaisesRegex(RuntimeError, "ingen API-nyckel för DeepSeek"):
                ai.svar(cfg, [], "system")
        post.assert_not_called()

    def test_deepseek_is_available_in_config_and_model_choices(self):
        cfg = ai.las_config("/path/that/does/not/exist")
        self.assertEqual(cfg["deepseek_modell"], "deepseek-flash")
        self.assertIn(("deepseek", "deepseek-flash"), ai.val_modeller(cfg))
        self.assertIn("DeepSeek", ai.beskrivning({**cfg, "backend": "deepseek"}))


if __name__ == "__main__":
    unittest.main()
