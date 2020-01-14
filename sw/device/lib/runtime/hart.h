// Copyright lowRISC contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#ifndef SW_DEVICE_LIB_RUNTIME_HART_H_
#define SW_DEVICE_LIB_RUNTIME_HART_H_

#include <stddef.h>
#include <stdnoreturn.h>

#include "sw/device/lib/base/stdasm.h"

/**
 * This header provides functions for controlling the excution of a hart, such
 * as halt-like functionality.
 */

/**
 * Hints to the processor that we don't have anything better to be doing, and to
 * go into low-power mode until an interrupt is serviced.
 *
 * This function may behave as if it is a no-op.
 */
inline void wait_for_interrupt(void) { asm volatile("wfi"); }

/**
 * Spin for roughly the given number of microseconds.
 *
 * @param microseconds the duration for which to spin.
 */
void busy_sleep_micros(size_t microseconds);

/**
 * Immediately halt program execution.
 *
 * This function conforms to the semantics defined in ISO C11 S7.22.4.1.
 */
noreturn void abort(void);

#endif  // SW_DEVICE_LIB_RUNTIME_HART_H_