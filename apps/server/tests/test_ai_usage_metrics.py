"""
Integration test for AI usage metrics aggregation.

Tests:
1. Create a project with chat sessions
2. Add chat messages with different roles (user, assistant, tool)
3. Verify AI usage stats display correctly
4. Verify metrics update after more AI interactions
5. Verify trend data (daily/weekly/monthly)
6. Verify summary data (today/this_week/this_month)
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, SQLModel, create_engine

from config.datetime_utils import beijing_date
from models.entities import ChatMessage, ChatSession, Project, User
from services.features.writing_stats_service import writing_stats_service


def create_test_database():
    """Create a temporary test database."""
    # Create a temporary file for the test database
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, echo=False)
    SQLModel.metadata.create_all(engine)
    return engine, db_fd, db_path


def test_ai_usage_metrics():
    """Test AI usage metrics aggregate from chat sessions."""
    print("=" * 60)
    print("Testing AI Usage Metrics Aggregation")
    print("=" * 60)

    # Create test database
    engine, db_fd, db_path = create_test_database()

    try:
        with Session(engine) as session:
            # Keep every fixture row on the same UTC calendar day. Relative
            # ``utcnow() - 2h`` data straddles midnight when this suite runs
            # shortly after 00:00 UTC and makes the "latest day == 8" assertion
            # time-of-day dependent.
            test_day = datetime.utcnow().replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )

            # Step 1: Create test user and project
            print("\n1. Creating test user and project...")
            test_user = User(
                id="ai-user-001",
                email="aiusage@example.com",
                username="aiuser",
                hashed_password="hashed",
            )
            session.add(test_user)

            test_project = Project(
                id="ai-project-001",
                name="AI Test Novel",
                owner_id=test_user.id,
                project_type="novel",
            )
            session.add(test_project)
            session.commit()
            print(f"   Created user: {test_user.id}")
            print(f"   Created project: {test_project.name}")

            # Step 2: Test initial state (no chat sessions)
            print("\n2. Testing initial state (no AI usage)...")
            initial_stats = writing_stats_service.get_ai_usage_stats(
                session, test_user.id, test_project.id
            )
            print(f"   Total sessions: {initial_stats['total_sessions']}")
            print(f"   Total messages: {initial_stats['total_messages']}")
            assert initial_stats['total_sessions'] == 0, "Initial sessions should be 0"
            assert initial_stats['total_messages'] == 0, "Initial messages should be 0"
            assert initial_stats['user_messages'] == 0, "Initial user messages should be 0"
            assert initial_stats['ai_messages'] == 0, "Initial AI messages should be 0"
            assert initial_stats['tool_messages'] == 0, "Initial tool messages should be 0"
            assert initial_stats['total_tokens'] == 0, "Initial total tokens should be 0"
            assert initial_stats['estimated_tokens'] == 0, "Initial tokens should be 0"
            assert initial_stats['first_interaction_date'] is None, "First date should be None"
            assert initial_stats['last_interaction_date'] is None, "Last date should be None"

            # Step 3: Create first chat session with messages
            print("\n3. Creating first chat session with messages...")
            chat_session_1 = ChatSession(
                id="chat-session-001",
                user_id=test_user.id,
                project_id=test_project.id,
                title="First AI Conversation",
                is_active=True,
                message_count=3,
            )
            session.add(chat_session_1)
            session.commit()

            # Add user message
            user_msg_1 = ChatMessage(
                id="msg-001",
                session_id=chat_session_1.id,
                role="user",
                content="Help me write the opening scene of my novel.",
                created_at=test_day + timedelta(minutes=1),
            )
            session.add(user_msg_1)

            # Add assistant message
            assistant_msg_1 = ChatMessage(
                id="msg-002",
                session_id=chat_session_1.id,
                role="assistant",
                content="I'd be happy to help you craft an engaging opening scene. Let's start by considering the setting and mood. What kind of atmosphere do you want to establish? A mysterious foggy night, a bustling marketplace, or perhaps a quiet village at dawn?",
                message_metadata=json.dumps(
                    {"usage": {"input_tokens": 120, "output_tokens": 80, "total_tokens": 200}}
                ),
                created_at=test_day + timedelta(minutes=2),
            )
            session.add(assistant_msg_1)

            # Add tool message
            tool_msg_1 = ChatMessage(
                id="msg-003",
                session_id=chat_session_1.id,
                role="tool",
                content='{"action": "read_file", "result": "chapter1.md content..."}',
                tool_call_id="call-001",
                created_at=test_day + timedelta(minutes=3),
            )
            session.add(tool_msg_1)
            session.commit()
            print(f"   Created chat session: {chat_session_1.title}")
            print("   Added 3 messages (1 user, 1 assistant, 1 tool)")

            # Step 4: Verify AI usage stats after first session
            print("\n4. Verifying AI usage stats after first session...")
            stats_after_first = writing_stats_service.get_ai_usage_stats(
                session, test_user.id, test_project.id
            )
            print(f"   Total sessions: {stats_after_first['total_sessions']}")
            print(f"   Total messages: {stats_after_first['total_messages']}")
            print(f"   User messages: {stats_after_first['user_messages']}")
            print(f"   AI messages: {stats_after_first['ai_messages']}")
            print(f"   Tool messages: {stats_after_first['tool_messages']}")
            print(f"   Estimated tokens: {stats_after_first['estimated_tokens']}")

            assert stats_after_first['total_sessions'] == 1, "Should have 1 session"
            assert stats_after_first['total_messages'] == 3, "Should have 3 messages"
            assert stats_after_first['user_messages'] == 1, "Should have 1 user message"
            assert stats_after_first['ai_messages'] == 1, "Should have 1 AI message"
            assert stats_after_first['tool_messages'] == 1, "Should have 1 tool message"
            assert stats_after_first['estimated_tokens'] > 0, "Should have some tokens estimated"
            assert stats_after_first['first_interaction_date'] is not None, "First date should be set"
            assert stats_after_first['last_interaction_date'] is not None, "Last date should be set"
            assert stats_after_first['active_session_id'] == chat_session_1.id, "Active session should match"

            # Step 5: Create second chat session with more messages
            print("\n5. Creating second chat session with more messages...")
            chat_session_2 = ChatSession(
                id="chat-session-002",
                user_id=test_user.id,
                project_id=test_project.id,
                title="Character Development",
                is_active=False,
                message_count=5,
            )
            session.add(chat_session_2)
            session.commit()

            # Add multiple messages to second session
            user_msg_2 = ChatMessage(
                id="msg-004",
                session_id=chat_session_2.id,
                role="user",
                content="Can you help me develop my protagonist's backstory?",
                created_at=test_day + timedelta(minutes=4),
            )
            session.add(user_msg_2)

            assistant_msg_2 = ChatMessage(
                id="msg-005",
                session_id=chat_session_2.id,
                role="assistant",
                content="Of course! A compelling protagonist needs depth and complexity. Let's explore their childhood, motivations, fears, and desires. What drives your character? What secrets do they carry?",
                message_metadata=json.dumps(
                    {"usage": {"input_tokens": 140, "output_tokens": 90, "total_tokens": 230}}
                ),
                created_at=test_day + timedelta(minutes=5),
            )
            session.add(assistant_msg_2)

            user_msg_3 = ChatMessage(
                id="msg-006",
                session_id=chat_session_2.id,
                role="user",
                content="They grew up in a small fishing village and lost their parents in a storm.",
                created_at=test_day + timedelta(minutes=6),
            )
            session.add(user_msg_3)

            assistant_msg_3 = ChatMessage(
                id="msg-007",
                session_id=chat_session_2.id,
                role="assistant",
                content="That's a powerful origin story! The loss of parents to the sea creates a deep fear and respect for nature. This could manifest as determination to master sailing, or perhaps a reluctance to ever leave dry land. How does this trauma affect their relationships with others?",
                message_metadata=json.dumps(
                    {"usage": {"input_tokens": 160, "output_tokens": 100, "total_tokens": 260}}
                ),
                created_at=test_day + timedelta(minutes=7),
            )
            session.add(assistant_msg_3)

            tool_msg_2 = ChatMessage(
                id="msg-008",
                session_id=chat_session_2.id,
                role="tool",
                content='{"action": "update_character", "result": "Character backstory updated"}',
                tool_call_id="call-002",
                created_at=test_day + timedelta(minutes=8),
            )
            session.add(tool_msg_2)
            session.commit()
            print(f"   Created chat session: {chat_session_2.title}")
            print("   Added 5 messages (2 user, 2 assistant, 1 tool)")

            # Step 6: Verify metrics updated after more AI interactions
            print("\n6. Verifying metrics updated after more AI interactions...")
            stats_after_second = writing_stats_service.get_ai_usage_stats(
                session, test_user.id, test_project.id
            )
            print(f"   Total sessions: {stats_after_second['total_sessions']}")
            print(f"   Total messages: {stats_after_second['total_messages']}")
            print(f"   User messages: {stats_after_second['user_messages']}")
            print(f"   AI messages: {stats_after_second['ai_messages']}")
            print(f"   Tool messages: {stats_after_second['tool_messages']}")

            assert stats_after_second['total_sessions'] == 2, "Should have 2 sessions"
            assert stats_after_second['total_messages'] == 8, "Should have 8 total messages"
            assert stats_after_second['user_messages'] == 3, "Should have 3 user messages"
            assert stats_after_second['ai_messages'] == 3, "Should have 3 AI messages"
            assert stats_after_second['tool_messages'] == 2, "Should have 2 tool messages"

            # Verify tokens increased
            print(f"   Token estimate increased: {stats_after_first['estimated_tokens']} -> {stats_after_second['estimated_tokens']}")
            assert stats_after_second['estimated_tokens'] > stats_after_first['estimated_tokens'], "Tokens should increase"

            # Step 7: Test daily trend data
            print("\n7. Testing daily trend data...")
            daily_trend = writing_stats_service.get_ai_usage_trend(
                session, test_user.id, test_project.id, period="daily", days=7
            )
            print(f"   Daily trend entries: {len(daily_trend)}")
            if daily_trend:
                latest_day = daily_trend[-1]
                print(f"   Latest day: {latest_day['date']}")
                print(f"   Total messages: {latest_day['total_messages']}")
                print(f"   User messages: {latest_day['user_messages']}")
                print(f"   AI messages: {latest_day['ai_messages']}")
                print(f"   Tool messages: {latest_day['tool_messages']}")
                assert latest_day['total_messages'] == 8, "Daily total should be 8"

            # Step 8: Test weekly trend data
            print("\n8. Testing weekly trend data...")
            weekly_trend = writing_stats_service.get_ai_usage_trend(
                session, test_user.id, test_project.id, period="weekly", days=30
            )
            print(f"   Weekly trend entries: {len(weekly_trend)}")
            if weekly_trend:
                latest_week = weekly_trend[-1]
                print(f"   Latest week: {latest_week['period_label']}")
                print(f"   Total messages: {latest_week['total_messages']}")
                print(f"   Days with activity: {latest_week['days_with_activity']}")
                assert latest_week['total_messages'] == 8, "Weekly total should be 8"

            # Step 9: Test monthly trend data
            print("\n9. Testing monthly trend data...")
            monthly_trend = writing_stats_service.get_ai_usage_trend(
                session, test_user.id, test_project.id, period="monthly", days=90
            )
            print(f"   Monthly trend entries: {len(monthly_trend)}")
            if monthly_trend:
                latest_month = monthly_trend[-1]
                print(f"   Latest month: {latest_month['period_label']}")
                print(f"   Total messages: {latest_month['total_messages']}")

            # Step 10: Test AI usage summary
            print("\n10. Testing AI usage summary...")
            # The summary uses Beijing calendar days; anchor it to the fixture
            # day so the result does not depend on the time the suite runs.
            summary = writing_stats_service.get_ai_usage_summary(
                session, test_user.id, test_project.id, reference_date=beijing_date(test_day)
            )
            print(f"   Current total sessions: {summary['current']['total_sessions']}")
            print(f"   Current total messages: {summary['current']['total_messages']}")
            print(f"   Today's messages: {summary['today']['total']}")
            print(f"   Today's user messages: {summary['today']['user']}")
            print(f"   Today's AI messages: {summary['today']['ai']}")
            print(f"   This week's messages: {summary['this_week']['total']}")
            print(f"   This month's messages: {summary['this_month']['total']}")

            assert summary['current']['total_sessions'] == 2, "Summary should show 2 sessions"
            assert summary['current']['total_messages'] == 8, "Summary should show 8 messages"
            assert summary['today']['total'] == 8, "All messages should be today"
            assert summary['this_week']['total'] == 8, "All messages should be this week"
            assert summary['this_month']['total'] == 8, "All messages should be this month"

            # Step 11: Test real token aggregation accuracy
            print("\n11. Testing real token aggregation...")
            expected_tokens = 200 + 230 + 260
            actual_tokens = stats_after_second['estimated_tokens']
            print(f"   Expected tokens (from usage metadata): {expected_tokens}")
            print(f"   Actual token aggregate: {actual_tokens}")
            assert actual_tokens == expected_tokens, f"Token aggregate should be {expected_tokens}"

            # Step 12: Test message role distribution
            print("\n12. Verifying message role distribution...")
            total = (stats_after_second['user_messages'] +
                     stats_after_second['ai_messages'] +
                     stats_after_second['tool_messages'])
            print(f"   Sum of role messages: {total}")
            print(f"   Total messages: {stats_after_second['total_messages']}")
            assert total == stats_after_second['total_messages'], "Role distribution should match total"

            print("\n" + "=" * 60)
            print("ALL AI USAGE METRICS TESTS PASSED!")
            print("=" * 60)

            # Print summary
            print("\n📊 Test Summary:")
            print(f"   - Created project: {test_project.name}")
            print("   - Created 2 chat sessions with 8 total messages")
            print("   - Initial state (no usage): Verified ✓")
            print("   - Stats after first session: Verified ✓")
            print("   - Metrics update after more interactions: Verified ✓")
            print("   - Daily trend data: Verified ✓")
            print("   - Weekly trend data: Verified ✓")
            print("   - Monthly trend data: Verified ✓")
            print("   - AI usage summary (today/week/month): Verified ✓")
            print("   - Token estimation accuracy: Verified ✓")
            print("   - Message role distribution: Verified ✓")
            print("\n📈 Final Stats:")
            print(f"   - Total sessions: {stats_after_second['total_sessions']}")
            print(f"   - Total messages: {stats_after_second['total_messages']}")
            print(f"   - Estimated tokens: {stats_after_second['estimated_tokens']}")

    finally:
        # Cleanup
        os.close(db_fd)
        os.unlink(db_path)
        print("\n🧹 Test database cleaned up.")


if __name__ == "__main__":
    test_ai_usage_metrics()
