.PHONY: start stop status setup clean logs shell send-test-emails help

DEMO_CONFIG := $(CURDIR)/config.yaml

help:
	@echo "Clerk Demo Environment"
	@echo "======================"
	@echo ""
	@echo "Quick start:"
	@echo "  make start      - Start mock email server"
	@echo "  make setup      - Configure clerk to use demo server"
	@echo "  make stop       - Stop mock email server"
	@echo ""
	@echo "Utilities:"
	@echo "  make status     - Check if server is running"
	@echo "  make logs       - View server logs"
	@echo "  make send-test  - Send some test emails to play with"
	@echo "  make shell      - Open clerk interactive shell"
	@echo "  make clean      - Stop server and remove demo config"
	@echo ""
	@echo "After 'make start' and 'make setup', try:"
	@echo "  clerk inbox --fresh"
	@echo "  clerk shell"
	@echo ""

start:
	@echo "Starting mock email server..."
	@docker-compose up -d
	@echo "Waiting for server to be ready..."
	@sleep 3
	@echo ""
	@echo "Mock email server running!"
	@echo "  IMAP: localhost:3143"
	@echo "  SMTP: localhost:3025"
	@echo ""
	@echo "Run 'make setup' to configure clerk"

stop:
	@echo "Stopping mock email server..."
	@docker-compose down
	@echo "Server stopped."

status:
	@docker-compose ps

logs:
	@docker-compose logs -f

setup:
	@echo "Creating demo configuration..."
	@mkdir -p ~/.config/clerk
	@if [ -f ~/.config/clerk/config.yaml ]; then \
		echo "Backing up existing config to ~/.config/clerk/config.yaml.bak"; \
		cp ~/.config/clerk/config.yaml ~/.config/clerk/config.yaml.bak; \
	fi
	@cp config.yaml ~/.config/clerk/config.yaml
	@echo ""
	@echo "Demo config installed! You can now use clerk:"
	@echo "  clerk status"
	@echo "  clerk inbox --fresh"
	@echo "  clerk shell"
	@echo ""
	@echo "To restore your original config later:"
	@echo "  cp ~/.config/clerk/config.yaml.bak ~/.config/clerk/config.yaml"

send-test:
	@echo "Sending test emails..."
	@python3 send_test_emails.py
	@echo ""
	@echo "Test emails sent! Run 'clerk inbox --fresh' to see them"

shell:
	@clerk shell

clean:
	@echo "Cleaning up..."
	@docker-compose down -v 2>/dev/null || true
	@if [ -f ~/.config/clerk/config.yaml.bak ]; then \
		echo "Restoring original config..."; \
		mv ~/.config/clerk/config.yaml.bak ~/.config/clerk/config.yaml; \
	else \
		echo "Removing demo config..."; \
		rm -f ~/.config/clerk/config.yaml; \
	fi
	@echo "Cleanup complete."
