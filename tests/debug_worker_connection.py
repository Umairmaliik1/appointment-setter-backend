"""
Diagnostic script to check LiveKit worker connection and room creation.
Run this to verify if rooms are being created and if worker can access them.
"""
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from livekit import api
from app.core.config import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET

async def check_livekit_connection():
    """Check LiveKit connection and list recent rooms."""
    print("=" * 80)
    print("LIVEKIT CONNECTION DIAGNOSTICS")
    print("=" * 80)
    
    # Check environment variables
    print("\n[1] Environment Variables:")
    print(f"  LIVEKIT_URL: {LIVEKIT_URL or '❌ NOT SET'}")
    print(f"  LIVEKIT_API_KEY: {'✓ SET (' + LIVEKIT_API_KEY[:10] + '...)' if LIVEKIT_API_KEY else '❌ NOT SET'}")
    print(f"  LIVEKIT_API_SECRET: {'✓ SET (' + LIVEKIT_API_SECRET[:10] + '...)' if LIVEKIT_API_SECRET else '❌ NOT SET'}")
    
    if not LIVEKIT_URL or not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        print("\n❌ ERROR: LiveKit credentials are missing!")
        print("   Please check your .env file and ensure all LIVEKIT_* variables are set.")
        return
    
    # Try to connect to LiveKit
    print("\n[2] Connecting to LiveKit...")
    try:
        livekit_api = api.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET
        )
        print(f"  ✓ Connected to: {LIVEKIT_URL}")
    except Exception as e:
        print(f"  ❌ Failed to connect: {e}")
        return
    
    # List recent rooms
    print("\n[3] Checking Recent Rooms...")
    try:
        rooms_response = await livekit_api.room.list_rooms(api.ListRoomsRequest())
        rooms = rooms_response.rooms if hasattr(rooms_response, 'rooms') else []
        
        print(f"  Total rooms found: {len(rooms)}")
        
        if len(rooms) == 0:
            print("  ⚠️  No rooms found. This could mean:")
            print("     - No calls have been made recently")
            print("     - Rooms are being created but immediately deleted")
            print("     - You're connected to a different LiveKit server than the deployed backend")
        else:
            print("\n  Recent rooms:")
            inbound_rooms = [r for r in rooms if r.name.startswith("inbound-")]
            other_rooms = [r for r in rooms if not r.name.startswith("inbound-")]
            
            if inbound_rooms:
                print(f"\n  📞 Inbound call rooms ({len(inbound_rooms)}):")
                for room in sorted(inbound_rooms, key=lambda r: r.creation_time, reverse=True)[:10]:
                    created_time = room.creation_time if hasattr(room, 'creation_time') else 'unknown'
                    participants = room.num_participants if hasattr(room, 'num_participants') else 0
                    print(f"    - {room.name} (participants: {participants}, created: {created_time})")
            
            if other_rooms:
                print(f"\n  🔷 Other rooms ({len(other_rooms)}):")
                for room in sorted(other_rooms, key=lambda r: r.creation_time, reverse=True)[:5]:
                    created_time = room.creation_time if hasattr(room, 'creation_time') else 'unknown'
                    participants = room.num_participants if hasattr(room, 'num_participants') else 0
                    print(f"    - {room.name} (participants: {participants}, created: {created_time})")
                
    except Exception as e:
        print(f"  ❌ Failed to list rooms: {e}")
        import traceback
        traceback.print_exc()
    
    # Check SIP trunks
    print("\n[4] Checking SIP Configuration...")
    try:
        sip_service = livekit_api.sip
        if hasattr(sip_service, 'list_sip_inbound_trunk'):
            # Try different API call patterns
            try:
                # Pattern 1: Try with empty request object
                if hasattr(api, 'ListSIPInboundTrunkRequest'):
                    trunks_response = await sip_service.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
                else:
                    # Pattern 2: Try calling directly (may take no args or empty request)
                    trunks_response = await sip_service.list_sip_inbound_trunk()
                
                # Extract trunks from response
                trunks_list = []
                if hasattr(trunks_response, 'inbound_trunks'):
                    trunks_list = trunks_response.inbound_trunks
                elif isinstance(trunks_response, list):
                    trunks_list = trunks_response
                elif hasattr(trunks_response, 'items'):
                    trunks_list = list(trunks_response.items)
                
                if trunks_list:
                    print(f"  ✓ Found {len(trunks_list)} SIP inbound trunk(s):")
                    for trunk in trunks_list[:5]:  # Show first 5
                        trunk_id = getattr(trunk, 'inbound_trunk_id', None) or getattr(trunk, 'trunk_id', None) or getattr(trunk, 'id', None) or 'unknown'
                        numbers = getattr(trunk, 'numbers', []) or []
                        name = getattr(trunk, 'name', None) or 'unnamed'
                        print(f"    - {name} (ID: {trunk_id}, numbers: {numbers})")
                else:
                    print("  ⚠️  No SIP inbound trunks found")
                    print("     This could be the issue - SIP trunks must be configured for calls to work")
            except TypeError as te:
                print(f"  ⚠️  API signature issue: {te}")
                print("     (This is a diagnostic script limitation - SIP trunks may still exist)")
        else:
            print("  ⚠️  SIP service does not have 'list_sip_inbound_trunk' method")
    except Exception as e:
        print(f"  ⚠️  Could not check SIP trunks: {e}")
        print("     (This is a diagnostic script limitation - SIP trunks may still exist)")
    
    # Check dispatch rules
    print("\n[5] Checking Dispatch Rules...")
    try:
        sip_service = livekit_api.sip
        if hasattr(sip_service, 'list_sip_dispatch_rule') or hasattr(sip_service, 'list_sip_dispatch_rules'):
            # Try different method names
            list_method = getattr(sip_service, 'list_sip_dispatch_rule', None) or getattr(sip_service, 'list_sip_dispatch_rules', None)
            
            if list_method:
                try:
                    # Try with empty request or no args
                    if hasattr(api, 'ListSIPDispatchRuleRequest'):
                        rules_response = await list_method(api.ListSIPDispatchRuleRequest())
                    else:
                        rules_response = await list_method()
                    
                    # Extract rules from response
                    rules_list = []
                    if hasattr(rules_response, 'dispatch_rules'):
                        rules_list = rules_response.dispatch_rules
                    elif isinstance(rules_response, list):
                        rules_list = rules_response
                    elif hasattr(rules_response, 'items'):
                        rules_list = list(rules_response.items)
                    
                    if rules_list:
                        print(f"  ✓ Found {len(rules_list)} dispatch rule(s):")
                        for rule in rules_list[:5]:  # Show first 5
                            rule_id = getattr(rule, 'dispatch_rule_id', None) or getattr(rule, 'id', None) or 'unknown'
                            name = getattr(rule, 'name', None) or 'unnamed'
                            rule_info = getattr(rule, 'rule', None)
                            prefix = 'unknown'
                            if rule_info:
                                individual = getattr(rule_info, 'dispatch_rule_individual', None)
                                if individual:
                                    prefix = getattr(individual, 'room_prefix', None) or 'unknown'
                            print(f"    - {name} (ID: {rule_id}, room_prefix: {prefix})")
                    else:
                        print("  ⚠️  No dispatch rules found")
                        print("     ⚠️  CRITICAL: Dispatch rules route SIP calls to rooms")
                        print("     This is likely the issue - dispatch rules must be configured!")
                except TypeError as te:
                    print(f"  ⚠️  API signature issue: {te}")
                    print("     (This is a diagnostic script limitation - dispatch rules may still exist)")
            else:
                print("  ⚠️  Could not find dispatch rule listing method")
        else:
            print("  ⚠️  SIP service does not have dispatch rule listing methods")
    except Exception as e:
        print(f"  ⚠️  Could not check dispatch rules: {e}")
        print("     ⚠️  CRITICAL: Dispatch rules route SIP calls to rooms")
        print("     Please check LiveKit Dashboard or verify SIP configuration manually")
    
    print("\n" + "=" * 80)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 80)
    print("\nNext Steps:")
    print("1. Make a test call NOW")
    print("2. Run this script again immediately after the call")
    print("3. Check if a room with 'inbound-' prefix appears")
    print("4. Check if the room has participants > 0")
    print("\nIf no room appears:")
    print("  - The deployed backend may not be creating rooms correctly")
    print("  - Check deployed backend logs for errors")
    print("  - Verify the deployed backend has the latest code")
    print("\nIf room appears but participants = 0:")
    print("  - SIP participant is not joining the room")
    print("  - Check SIP dispatch rules configuration")
    print("  - Check LiveKit Dashboard for SIP errors")
    print("\nNote: Unclosed session warnings are harmless and can be ignored")

if __name__ == "__main__":
    try:
        asyncio.run(check_livekit_connection())
    except KeyboardInterrupt:
        print("\n\nDiagnostic interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


