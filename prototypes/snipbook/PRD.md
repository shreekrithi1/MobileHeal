# SnipBook

> Simple salon bookings that just work

## Idea
A booking app for my hair salon: customers pick a service and a time, I see the day's appointments and mark them done.

## Problem
Independent salons struggle with missed calls, double-bookings, and messy calendars. Owners need a simple way for clients to self-book and a clear daily view to track and complete appointments.

## Personas
- **Salon Owner** — Fill the calendar, avoid double-bookings, and quickly mark appointments as completed.
- **Stylist** — See my day at a glance and know which services and clients are next.
- **Customer** — Book a service at a convenient time in seconds and receive confirmation.

## MVP features
- Customer self-booking by service and time (Must)
- Service catalog management (name, duration, price) (Must)
- Daily calendar view for the salon (Must)
- Prevent double-booking per staff and time slot (Must)
- Mark appointment as done/completed (Must)
- Confirm/cancel/reschedule appointments (Should)
- Basic customer profile capture (name, phone, email) (Must)
- Email/SMS booking confirmations and reminders (Should)
- Notes on appointments for special requests (Should)
- Buffer time between services (Could)

## User stories
- As **Customer**, I want to pick a service and available time slot, so I can book without calling the salon.
  - [ ] I can see available time slots for my chosen service
  - [ ] Booking confirms instantly if the slot is free
  - [ ] I receive a confirmation with date, time, and service
- As **Salon Owner**, I want to view today's appointments in a single calendar, so I can prepare and manage the day efficiently.
  - [ ] I can filter by staff member
  - [ ] Appointments show customer name, service, and time
  - [ ] Past times are visually distinct from future slots
- As **Stylist**, I want to mark an appointment as completed, so the schedule stays accurate and metrics are tracked.
  - [ ] I can change status from booked to completed
  - [ ] Completed appointments are visually distinct
  - [ ] Status change is timestamped
- As **System**, I want to prevent double-booking of the same staff, so no two appointments overlap for a stylist.
  - [ ] Overlapping bookings for the same staff are blocked
  - [ ] User receives a clear error message with alternatives
  - [ ] Calendar updates reflect the reserved time instantly
- As **Salon Owner**, I want to manage services with duration and price, so customers can book accurately and pricing is clear.
  - [ ] I can add, edit, deactivate services
  - [ ] Duration drives available time slots
  - [ ] Price is shown to customers during booking
- As **Customer**, I want to reschedule or cancel my appointment, so I can adjust plans without calling.
  - [ ] I can select a new available time for the same service
  - [ ] I receive updated confirmation
  - [ ] Past appointments cannot be rescheduled

## Success metrics
- Weekly completed bookings
- Calendar utilization rate (booked hours / available hours)
- No-show/cancellation rate
