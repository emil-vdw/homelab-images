import json
import unittest

import httpx2

from mealie_mcp.mealie import Changes, Mealie, MealieError

PARSED = {
    "quantity": 2.0,
    "unit": {"id": "u1", "name": "tablespoon"},
    "food": {"id": "f1", "name": "olive oil"},
    "note": "",
    "display": "2 tablespoons olive oil",
    "title": "Base",
    "referenceId": "r1",
}


def recipe(**overrides):
    return {
        "id": "id-1",
        "slug": "curry",
        "name": "Curry",
        "description": "Hot",
        "createdAt": "2026-11-01T10:00:00Z",
        "updatedAt": "2026-11-02T10:00:00Z",
        "recipeServings": 4.0,
        "recipeYield": "",
        "prepTime": "10 minutes",
        "cookTime": None,
        "totalTime": None,
        "recipeIngredient": [PARSED, {"note": "1 tsp chili flakes", "display": "1 tsp chili flakes", "referenceId": "r2"}],
        "recipeInstructions": [{"id": "s1", "text": "Fry.", "ingredientReferences": [{"referenceId": "r1"}]}],
        "notes": [{"title": "", "text": "Freezes well"}],
        **overrides,
    }


class FakeMealie:
    """Records requests and answers them like Mealie's API."""

    def __init__(self, stored):
        self.stored = stored
        self.requests = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        path = request.url.path
        if request.headers["Authorization"] != "Bearer secret":
            return httpx2.Response(401, json={"detail": "Not authenticated"})
        if path == "/api/users/self":
            return httpx2.Response(200, json={"groupSlug": "home"})
        if request.method == "GET" and path == "/api/recipes":
            return httpx2.Response(200, json={"items": [self.stored]})
        if request.method == "POST" and path == "/api/recipes":
            self.stored = recipe(slug="new", name=body["name"], recipeIngredient=[], recipeInstructions=[], notes=[])
            return httpx2.Response(201, json="new")
        if path == "/api/recipes/timeline/events":
            return httpx2.Response(201, json={})
        if path == f"/api/recipes/{self.stored['slug']}":
            if request.method == "PATCH":
                self.stored = {**self.stored, **body}
            return httpx2.Response(200, json=self.stored)
        return httpx2.Response(404, json={"detail": "Not found"})

    def calls(self, method):
        return [(path, body) for m, path, body in self.requests if m == method]


def client(fake, token="secret"):
    return Mealie("http://mealie", "https://recipes.example/", token, transport=httpx2.MockTransport(fake))


class SearchTest(unittest.TestCase):
    def search(self, *args):
        captured = []

        def handler(request):
            captured.append(dict(request.url.params))
            return httpx2.Response(200, json={"items": [recipe()]})

        results = Mealie("http://mealie", "https://r", "t", transport=httpx2.MockTransport(handler)).search(*args)
        return results, captured[0]

    def test_orders_by_recency(self):
        results, params = self.search("curry", "recently_updated", 5)
        self.assertEqual(params, {"perPage": "5", "search": "curry", "orderBy": "updatedAt", "orderDirection": "desc"})
        self.assertEqual(results[0]["slug"], "curry")

    def test_relevance_lists_everything_with_capped_page(self):
        _, params = self.search("", "relevance", 500)
        self.assertEqual(params, {"perPage": "50"})


class GetTest(unittest.TestCase):
    def test_presents_lines_and_link(self):
        view = client(FakeMealie(recipe())).get("curry")
        self.assertEqual(view["ingredients"], ["2 tablespoons olive oil", "1 tsp chili flakes"])
        self.assertEqual(view["instructions"], ["Fry."])
        self.assertEqual(view["notes"], ["Freezes well"])
        self.assertEqual(view["url"], "https://recipes.example/g/home/r/curry")
        self.assertEqual(view["cook_time"], "")

    def test_missing_recipe(self):
        with self.assertRaisesRegex(MealieError, "No such recipe"):
            client(FakeMealie(recipe())).get("other")

    def test_slug_cannot_leave_recipes(self):
        fake = FakeMealie(recipe())
        for slug in ["../users/self", "a/../../groups/self", "..", "curry?x=1", "Curry", "curry/", ""]:
            with self.subTest(slug=slug), self.assertRaisesRegex(MealieError, "No such recipe"):
                client(fake).get(slug)
        self.assertEqual(fake.requests, [])

    def test_rejection_does_not_leak_token(self):
        with self.assertRaises(MealieError) as raised:
            client(FakeMealie(recipe()), token="wrong").get("curry")
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("wrong", str(raised.exception))


class UpdateTest(unittest.TestCase):
    def test_keeps_parsed_data_for_unchanged_lines(self):
        fake = FakeMealie(recipe())
        client(fake).update("curry", Changes(ingredients=["2 tablespoons olive oil", " ", "1/4 tsp chili flakes"]), "milder")
        [(_, patch)] = fake.calls("PATCH")
        self.assertEqual(patch, {
            "recipeIngredient": [
                PARSED,
                {"note": "1/4 tsp chili flakes", "display": "1/4 tsp chili flakes", "originalText": "1/4 tsp chili flakes"},
            ]
        })

    def test_keeps_step_ids_and_links(self):
        fake = FakeMealie(recipe())
        client(fake).update("curry", Changes(instructions=["Fry.", "Serve."]), "add serving step")
        [(_, patch)] = fake.calls("PATCH")
        self.assertEqual(patch["recipeInstructions"][0]["id"], "s1")
        self.assertEqual(patch["recipeInstructions"][1], {"text": "Serve.", "ingredientReferences": []})

    def test_records_previous_values_before_writing(self):
        fake = FakeMealie(recipe())
        client(fake).update("curry", Changes(description="Mild", prep_time="10 minutes", ingredients=["2 tablespoons olive oil"]), "milder")
        methods = [m for m, path, _ in fake.requests if path != "/api/users/self"]
        self.assertEqual(methods, ["GET", "POST", "PATCH"])
        [(_, event)] = fake.calls("POST")
        self.assertEqual(event["recipeId"], "id-1")
        self.assertEqual(event["subject"], "Before AI edit: milder")
        # Prep time is unchanged, so only the altered fields are recorded.
        self.assertEqual(
            event["eventMessage"],
            "Description: Hot\n\nIngredients:\n- 2 tablespoons olive oil\n- 1 tsp chili flakes",
        )

    def test_unchanged_values_write_nothing(self):
        fake = FakeMealie(recipe())
        view = client(fake).update("curry", Changes(name="Curry", servings=4), "nothing")
        self.assertEqual(view["slug"], "curry")
        self.assertEqual(fake.calls("POST"), [])
        self.assertEqual(fake.calls("PATCH"), [])


class CreateTest(unittest.TestCase):
    def test_creates_then_fills_in_fields(self):
        fake = FakeMealie(recipe())
        view = client(fake).create(Changes(name="Tagine", servings=4, ingredients=["1 kg lamb"], instructions=["Cook."]))
        [(_, created)] = fake.calls("POST")
        [(path, patch)] = fake.calls("PATCH")
        self.assertEqual(created, {"name": "Tagine"})
        self.assertEqual(path, "/api/recipes/new")
        self.assertNotIn("name", patch)
        self.assertEqual(view["ingredients"], ["1 kg lamb"])

    def test_requires_name(self):
        with self.assertRaises(MealieError):
            client(FakeMealie(recipe())).create(Changes(name=""))


if __name__ == "__main__":
    unittest.main()
