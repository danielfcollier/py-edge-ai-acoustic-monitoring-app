"""
A robust ZMQ Probe Consumer to verify the Producer's output.
Features:
- Binds to the ZMQ port (Acting as the Server/Consumer).
- Tracks total data volume received.
- Handles Graceful Shutdown (Ctrl+C) to print final stats.
"""

import signal
import sys

import zmq


class ZmqProbeConsumer:
    def __init__(self, port: int = 5555):
        self._port = port
        self._total_bytes = 0
        self._running = True

    def _signal_handler(self, sig, frame):
        """Handles Ctrl+C and System Termination signals."""
        print("\n\n🛑 Shutdown signal received.")
        self._print_summary()
        self._running = False
        sys.exit(0)

    def _print_summary(self):
        """Calculates and prints the final data statistics."""
        kb_total = self._total_bytes / 1024
        mb_total = kb_total / 1024
        print("=" * 40)
        print("📊 PROBE SUMMARY")
        print(f"   Total Bytes: {self._total_bytes:,}")
        print(f"   Total Data : {kb_total:.2f} KB ({mb_total:.2f} MB)")
        print("=" * 40)

    def run(self):
        context = zmq.Context()
        socket = context.socket(zmq.PULL)

        # BIND logic (Server Mode)
        bind_address = f"tcp://*:{self._port}"

        try:
            socket.bind(bind_address)
            print(f"🚀 Probe Listening on {bind_address}")
            print("   Waiting for Producer connection... (Press Ctrl+C to stop)")

            # Register Signal Handlers
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)

            while self._running:
                # Blocking receive
                data = socket.recv()

                # Update Stats
                size = len(data)
                self._total_bytes += size

                # Live Log (Overwriting line for cleaner output, optional)
                # Using standard print for now to show flow
                print(f"⬇️  Received chunk: {size / 1024 / 1024:.1f} MB")

        except KeyboardInterrupt:
            # Fallback if signal handler doesn't catch it during blocking call
            self._print_summary()
        except Exception as e:
            print(f"❌ Error: {e}")
        finally:
            # Ensure context is cleaned up
            context.term()


if __name__ == "__main__":
    # You can change the port here if needed
    ZmqProbeConsumer(port=5555).run()
