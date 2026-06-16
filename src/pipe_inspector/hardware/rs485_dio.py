"""
rs485_dio.py

Reusable helper for a 16-point RS485 Modbus RTU Digital I/O module:
- Inputs:  I0 - I7  -> Modbus Discrete Inputs, Function 02
- Outputs: O0 - O7  -> Modbus Coils, Function 01/05/15

Default settings:
- port: /dev/ttyTHS1
- baudrate: 9600
- slave_id: 1

Install:
    pip install minimalmodbus pyserial

Example:
    from pipe_inspector.hardware.rs485_dio import RS485DIO

    io = RS485DIO()
    print(io.read_input(0))
    print(io.read_inputs([0, 1, 2]))

    io.write_output(0, 1)
    io.write_pulse(0, pulse_time=0.2)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence

import minimalmodbus
import serial

logger = logging.getLogger(__name__)


BitValue = int | bool


class RS485DIO:
    """
    RS485 Modbus RTU Digital I/O helper.

    Input range:
        0 - 7

    Output range:
        0 - 7
    """

    VALID_MIN = 0
    VALID_MAX = 7
    NUM_BITS = 8

    def __init__(
        self,
        port: str = "/dev/ttyTHS1",
        baudrate: int = 9600,
        slave_id: int = 1,
        timeout: float = 1.0,
        clear_outputs_on_start: bool = True,
    ) -> None:
        """
        Declare Modbus instrument.

        Args:
            port: Serial port, default "/dev/ttyTHS1"
            baudrate: Modbus baudrate, default 9600
            slave_id: Modbus slave ID, default 1
            timeout: Serial timeout in seconds
            clear_outputs_on_start: If True, set all outputs O0-O7 to 0 at startup
        """
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id

        try:
            self.instrument = minimalmodbus.Instrument(port, slave_id)
        except (serial.SerialException, FileNotFoundError, OSError) as exc:
            raise RuntimeError(f"Cannot open RS485 port {port!r}: {exc}") from exc

        self.instrument.serial.baudrate = baudrate
        self.instrument.serial.bytesize = 8
        self.instrument.serial.parity = serial.PARITY_NONE
        self.instrument.serial.stopbits = 1
        self.instrument.serial.timeout = timeout

        self.instrument.mode = minimalmodbus.MODE_RTU
        self.instrument.clear_buffers_before_each_transaction = True

        if clear_outputs_on_start:
            self.reset_outputs()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release the serial port so the OS makes it available again."""
        try:
            if self.instrument and self.instrument.serial and self.instrument.serial.is_open:
                self.instrument.serial.close()
                logger.info("RS485DIO: port %r closed.", self.port)
        except (serial.SerialException, OSError) as exc:
            logger.warning("RS485DIO: error closing port %r: %s", self.port, exc)

    def __del__(self) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_bit_index(self, bit: int, name: str = "bit") -> None:
        if not isinstance(bit, int):
            raise TypeError(f"{name} must be int, got {type(bit).__name__}")

        if not (self.VALID_MIN <= bit <= self.VALID_MAX):
            raise ValueError(
                f"Invalid {name}: {bit}. Valid range is {self.VALID_MIN}-{self.VALID_MAX}."
            )

    def _validate_bit_indices(self, bits: Sequence[int], name: str = "bits") -> list[int]:
        if not isinstance(bits, Sequence) or isinstance(bits, (str, bytes)):
            raise TypeError(f"{name} must be a sequence of int, e.g. [0, 1, 2]")

        if len(bits) == 0:
            raise ValueError(f"{name} cannot be empty.")

        validated = []
        for bit in bits:
            self._validate_bit_index(bit, name="bit")
            validated.append(bit)

        return validated

    def _validate_value(self, value: BitValue, name: str = "value") -> int:
        if value in (0, False):
            return 0
        if value in (1, True):
            return 1
        raise ValueError(f"{name} must be 0/1 or False/True, got {value!r}")

    def _validate_values(self, values: Sequence[BitValue], name: str = "values") -> list[int]:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise TypeError(f"{name} must be a sequence of 0/1 values.")

        if len(values) == 0:
            raise ValueError(f"{name} cannot be empty.")

        if len(values) > self.NUM_BITS:
            raise ValueError(f"{name} cannot have more than {self.NUM_BITS} values.")

        return [self._validate_value(v, name="value") for v in values]

    # ------------------------------------------------------------------
    # Output reset
    # ------------------------------------------------------------------

    def reset_outputs(self) -> None:
        """Set all outputs O0-O7 to 0."""
        self.write_outputs([0, 1, 2, 3, 4, 5, 6, 7], [0, 0, 0, 0, 0, 0, 0, 0])

    # ------------------------------------------------------------------
    # One-time read functions
    # ------------------------------------------------------------------

    def read_input(self, input_index: int) -> int:
        """
        Read one input bit once.

        Args:
            input_index: Input index 0-7

        Returns:
            0 or 1
        """
        self._validate_bit_index(input_index, name="input_index")

        return int(
            self.instrument.read_bit(
                registeraddress=input_index,
                functioncode=2,  # Read Discrete Inputs
            )
        )

    def read_inputs(self, input_indices: Sequence[int] | None = None) -> list[int]:
        """
        Read multiple input bits once.

        Args:
            input_indices:
                - None: read all inputs 0-7
                - list/tuple: read selected inputs, e.g. [0, 2, 5]

        Returns:
            List of 0/1 values in the same order as input_indices.
        """
        if input_indices is None:
            values = self.instrument.read_bits(
                registeraddress=0,
                number_of_bits=self.NUM_BITS,
                functioncode=2,  # Read Discrete Inputs
            )
            return [int(v) for v in values]

        indices = self._validate_bit_indices(input_indices, name="input_indices")

        # Read all 8 first, then select requested indices.
        # This is faster and simpler than many single requests.
        all_values = self.instrument.read_bits(
            registeraddress=0,
            number_of_bits=self.NUM_BITS,
            functioncode=2,
        )

        return [int(all_values[i]) for i in indices]

    # ------------------------------------------------------------------
    # Continuous read functions
    # ------------------------------------------------------------------

    def continue_read_input(
        self,
        input_index: int,
        mode: str = "always",
        interval: float = 0.1,
        callback: Callable[[int], None] | None = None,
        pulse_edge: str = "rising",
    ) -> None:
        """
        Continuously read one input bit.

        Args:
            input_index: Input index 0-7
            mode:
                - "always": return/callback every read
                - "pulse": return/callback only when edge is detected
            interval: Polling interval in seconds
            callback: Optional function called with value
            pulse_edge:
                - "rising": detect 0 -> 1
                - "falling": detect 1 -> 0
                - "both": detect any change

        Note:
            This function runs forever until Ctrl+C.
        """
        self._validate_bit_index(input_index, name="input_index")
        self._validate_continue_mode(mode)
        self._validate_pulse_edge(pulse_edge)

        last_value: int | None = None

        while True:
            value = self.read_input(input_index)

            if mode == "always":
                self._emit(value, callback)

            elif mode == "pulse":
                if last_value is not None and self._is_pulse(last_value, value, pulse_edge):
                    self._emit(value, callback)

            last_value = value
            time.sleep(interval)

    def continue_read_inputs(
        self,
        input_indices: Sequence[int] | None = None,
        mode: str = "always",
        interval: float = 0.1,
        callback: Callable[[list[int]], None] | None = None,
        pulse_edge: str = "rising",
    ) -> None:
        """
        Continuously read multiple input bits.

        Args:
            input_indices:
                - None: read all inputs 0-7
                - list/tuple: read selected inputs, e.g. [0, 1, 2]
            mode:
                - "always": return/callback every read
                - "pulse": return/callback only when any selected bit has edge
            interval: Polling interval in seconds
            callback: Optional function called with list of values
            pulse_edge:
                - "rising": detect 0 -> 1
                - "falling": detect 1 -> 0
                - "both": detect any change

        Note:
            This function runs forever until Ctrl+C.
        """
        self._validate_continue_mode(mode)
        self._validate_pulse_edge(pulse_edge)

        if input_indices is not None:
            self._validate_bit_indices(input_indices, name="input_indices")

        last_values: list[int] | None = None

        while True:
            values = self.read_inputs(input_indices)

            if mode == "always":
                self._emit(values, callback)

            elif mode == "pulse":
                if last_values is not None:
                    pulse_detected = any(
                        self._is_pulse(old, new, pulse_edge)
                        for old, new in zip(last_values, values)
                    )
                    if pulse_detected:
                        self._emit(values, callback)

            last_values = values
            time.sleep(interval)

    def _validate_continue_mode(self, mode: str) -> None:
        if mode not in ("always", "pulse"):
            raise ValueError('mode must be "always" or "pulse".')

    def _validate_pulse_edge(self, pulse_edge: str) -> None:
        if pulse_edge not in ("rising", "falling", "both"):
            raise ValueError('pulse_edge must be "rising", "falling", or "both".')

    def _is_pulse(self, old: int, new: int, pulse_edge: str) -> bool:
        if pulse_edge == "rising":
            return old == 0 and new == 1
        if pulse_edge == "falling":
            return old == 1 and new == 0
        if pulse_edge == "both":
            return old != new
        return False

    def _emit(self, data, callback) -> None:
        if callback is not None:
            callback(data)
        else:
            print(data)

    # ------------------------------------------------------------------
    # Write output functions
    # ------------------------------------------------------------------

    def write_output(self, output_index: int, value: BitValue) -> None:
        """
        Write one output bit.

        Args:
            output_index: Output index 0-7
            value: 0/1 or False/True
        """
        self._validate_bit_index(output_index, name="output_index")
        bit_value = self._validate_value(value)

        self.instrument.write_bit(
            registeraddress=output_index,
            value=bit_value,
            functioncode=5,  # Write Single Coil
        )

    def write_outputs(
        self,
        output_indices: Sequence[int],
        values: Sequence[BitValue],
    ) -> None:
        """
        Write multiple selected output bits.

        Args:
            output_indices: Output indices 0-7, e.g. [0, 2, 5]
            values: Values in same order, e.g. [1, 0, 1]

        Note:
            This method preserves outputs that are not selected by reading current
            output states first, then writing all O0-O7 in one request.
        """
        indices = self._validate_bit_indices(output_indices, name="output_indices")
        bit_values = self._validate_values(values)

        if len(indices) != len(bit_values):
            raise ValueError(
                f"output_indices length ({len(indices)}) must match values length ({len(bit_values)})."
            )

        current = self.read_outputs()

        for idx, val in zip(indices, bit_values):
            current[idx] = val

        self.instrument.write_bits(
            registeraddress=0,
            values=current,  # Function 15: Write Multiple Coils
        )

    def write_all_outputs(self, values: Sequence[BitValue]) -> None:
        """
        Write all outputs O0-O7 at once.

        Args:
            values: exactly 8 values, e.g. [1,0,0,0,0,0,0,0]
        """
        bit_values = self._validate_values(values)

        if len(bit_values) != self.NUM_BITS:
            raise ValueError(f"values must contain exactly {self.NUM_BITS} bits.")

        self.instrument.write_bits(registeraddress=0, values=bit_values)

    def read_output(self, output_index: int) -> int:
        """
        Read one output coil status once.

        Args:
            output_index: Output index 0-7

        Returns:
            0 or 1
        """
        self._validate_bit_index(output_index, name="output_index")

        return int(
            self.instrument.read_bit(
                registeraddress=output_index,
                functioncode=1,  # Read Coils
            )
        )

    def read_outputs(self, output_indices: Sequence[int] | None = None) -> list[int]:
        """
        Read output coil status.

        Args:
            output_indices:
                - None: read all outputs O0-O7
                - list/tuple: read selected outputs

        Returns:
            List of 0/1 values.
        """
        if output_indices is None:
            values = self.instrument.read_bits(
                registeraddress=0,
                number_of_bits=self.NUM_BITS,
                functioncode=1,  # Read Coils
            )
            return [int(v) for v in values]

        indices = self._validate_bit_indices(output_indices, name="output_indices")

        all_values = self.instrument.read_bits(
            registeraddress=0,
            number_of_bits=self.NUM_BITS,
            functioncode=1,
        )

        return [int(all_values[i]) for i in indices]

    # ------------------------------------------------------------------
    # Pulse output functions
    # ------------------------------------------------------------------

    def write_pulse(
        self,
        output_index: int,
        pulse_time: float = 0.2,
        active_value: BitValue = 1,
    ) -> None:
        """
        Send pulse to one output.

        Example:
            write_pulse(0, pulse_time=0.2)
            O0 ON for 0.2 sec, then OFF

        Args:
            output_index: Output index 0-7
            pulse_time: Pulse duration in seconds
            active_value: 1 for active-high pulse, 0 for active-low pulse
        """
        self._validate_bit_index(output_index, name="output_index")
        active = self._validate_value(active_value, name="active_value")
        inactive = 0 if active == 1 else 1

        self.write_output(output_index, active)
        time.sleep(pulse_time)
        self.write_output(output_index, inactive)

    def write_pulses(
        self,
        output_indices: Sequence[int],
        pulse_time: float = 0.2,
        active_value: BitValue = 1,
    ) -> None:
        """
        Send pulse to multiple outputs at the same time.

        Example:
            write_pulses([0, 1, 2], pulse_time=0.2)
            O0, O1, O2 ON for 0.2 sec, then OFF

        Args:
            output_indices: Output indices 0-7
            pulse_time: Pulse duration in seconds
            active_value: 1 for active-high pulse, 0 for active-low pulse
        """
        indices = self._validate_bit_indices(output_indices, name="output_indices")
        active = self._validate_value(active_value, name="active_value")
        inactive = 0 if active == 1 else 1

        self.write_outputs(indices, [active] * len(indices))
        time.sleep(pulse_time)
        self.write_outputs(indices, [inactive] * len(indices))


# ----------------------------------------------------------------------
# Simple manual test when running this file directly
# ----------------------------------------------------------------------

if __name__ == "__main__":
    io = RS485DIO()

    print("All outputs reset to 0.")
    print("Read all inputs:", io.read_inputs())
    print("Read all outputs:", io.read_outputs())

    print("Turn O0 ON")
    io.write_output(0, 1)
    time.sleep(1)

    print("Turn O0 OFF")
    io.write_output(0, 0)

    print("Pulse O1")
    io.write_pulse(1, pulse_time=0.2)

    print("Done.")
