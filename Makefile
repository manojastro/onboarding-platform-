# One-command workflows. On Windows without make, run the docker compose
# command shown in each target directly.
COMPOSE := docker compose

.PHONY: up down reset logs ps test test-backend test-mock test-web lint

up: ## Build and start the whole system
	$(COMPOSE) up --build -d

down: ## Stop everything (data volumes are kept)
	$(COMPOSE) down

reset: ## Stop everything and delete all data
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test: test-backend test-mock test-web ## Every test suite, inside containers

test-backend:
	$(COMPOSE) run --rm --build api pytest -q

test-mock:
	$(COMPOSE) run --rm --build mock-destination pytest -q

test-web:
	docker build --target test -t onboarding-web-test ./frontend
	docker run --rm onboarding-web-test

lint:
	$(COMPOSE) run --rm --build api ruff check .
