import unittest

from company_identity import extract_company_identity, normalize_cui, valid_romanian_cui


class CompanyIdentityTests(unittest.TestCase):
    def test_cui_checksum(self):
        self.assertEqual(normalize_cui("RO 14399840"), "RO14399840")
        self.assertTrue(valid_romanian_cui("RO14399840"))
        self.assertFalse(valid_romanian_cui("RO14399841"))

    def test_jsonld_organization(self):
        page = extract_company_identity(b'''
        <html><script type="application/ld+json">{
          "@type":"Organization", "legalName":"Exemplu Digital SRL",
          "taxID":"14399840", "address":{"streetAddress":"Str. Test 1",
          "addressLocality":"Bucuresti", "addressCountry":"RO"}
        }</script><body>Acasa</body></html>''', "https://example.ro/")
        self.assertEqual(page["company_names"][0]["name"], "Exemplu Digital SRL")
        self.assertEqual(page["cuis"][0]["cui"], "RO14399840")
        self.assertTrue(page["cuis"][0]["valid_checksum"])
        self.assertIn("Bucuresti", page["addresses"][0]["address"])

    def test_labeled_legal_text(self):
        page = extract_company_identity(b'''
        <html><body><p>Operatorul site-ului este EXEMPLU ONLINE S.R.L., cu sediul social:
        Strada Lalelelor nr. 10, Cluj-Napoca, CUI RO14399840, J12/345/2020.</p></body></html>
        ''', "https://example.ro/termeni")
        self.assertTrue(any("EXEMPLU ONLINE" in row["name"] for row in page["company_names"]))
        self.assertEqual(page["cuis"][0]["cui"], "RO14399840")
        self.assertEqual(page["registration_numbers"][0]["registration_number"], "J12/345/2020")
        self.assertTrue(any("Lalelelor" in row["address"] for row in page["addresses"]))

    def test_common_words_are_not_legal_suffixes(self):
        page = extract_company_identity(
            b"<html><body><p>Daca doriti sa continuati, apasati aici. This applies if accepted.</p></body></html>",
            "https://example.ro/privacy",
        )
        self.assertEqual(page["company_names"], [])

    def test_generic_address_language_is_not_registered_address(self):
        page = extract_company_identity(
            "<html><body>Vă puteți adresa autorității sau ne puteți scrie la adresă.</body></html>".encode(),
            "https://example.ro/privacy",
        )
        self.assertEqual(page["addresses"], [])

    def test_company_match_does_not_include_sentence_context(self):
        page = extract_company_identity(
            "<html><body><p>Datele sunt procesate de S.C. Exemplu Digital S.R.L.</p></body></html>".encode(),
            "https://example.ro/privacy",
        )
        names = [row["normalized"] for row in page["company_names"]]
        self.assertIn("EXEMPLU DIGITAL SRL", names)
        self.assertNotIn("DATELE SUNT PROCESATE DE S C EXEMPLU DIGITAL SRL", names)


if __name__ == "__main__":
    unittest.main()
