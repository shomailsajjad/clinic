# Testing your clinic software

Clinic: **Sajjad Poly Clinic and Diagnostic Center**. Doctor: **Dr. Asfa Batool (FCPS Radiology, MBBS)**.

Use invented patients and sample prices until the clinic deployment is verified.
The main page now has a locally bundled health-themed background; it does not
need internet to display.

## Recommended setup

Use a browser-based application on the clinic's local network. One Windows host
holds the application and PostgreSQL database; the admin, operators and doctor
open it in their browsers from separate Windows computers. Normal work does not
need internet. Keep the host on during clinic hours, protect it with a UPS, and
back up to your external drive. This fits your current offline requirement and
allows future online hosting without rebuilding every desktop installation.

The current single-computer development setup uses SQLite. Windows host setup,
PostgreSQL integration, LAN access and printer behavior have not yet been
validated. Public internet hosting is optional; no subscription is needed to
design or review the offline application. A public hosted demo has not been
deployed.

## Admin setup

1. Create the admin using the Windows setup launcher. Choose your own password.
2. In **Users & access**, create two operators and a doctor, each with a separate
   username and password. Roles are enforced on the server.
3. In **Services**, add Ultrasound, X-ray, CT, MRI and Doctor Consultation. Enter
   actual PKR prices and distinct token prefixes. Prices in screenshots are
   illustrative, not your agreed rates.
4. In **Diseases & diagnoses**, enter the structured disease/diagnosis options
   that the doctor will select for patient analysis. Disease codes are optional.
5. In **Report templates**, choose a service and create your report layout. Each
   measurement is entered on its own line, for example `Liver size | cm`. Use
   `[Liver size]` in the findings or impression to insert the doctor's value.
   The doctor must review the medical content; example templates are not
   diagnostic guidance. Word/PDF template import is not implemented.

## Operator: registration to receipt

1. Search **Patients** before registering someone new. CNIC is optional, including
   for children; a family may share a phone number. Enter the actual birth date
   or an approximate age in years/months/days, not an invented birth date.
2. Open the patient and click **Book this patient**, or open **Bookings → Book
   patient**. Choose one or more services and appointment/walk-in type.
3. Walk-ins use today's date. Appointments need a date and time. Booking issues
   a separate token per service on its scheduled date. Counters restart daily;
   two services can both have token 001. Cancelled numbers are never reused.
4. To discount a service, click **Request discount** and give an amount and reason.
   Wait for admin approval before collecting payment. Admin approves or rejects
   under **Discount approvals**.
5. Click **Collect payment** and enter the full approved amount. Select cash,
   bank transfer, card, Easypaisa or JazzCash. Digital payments require a receipt
   or reference number. Partial payments are not included in the initial scope.
6. Open the receipt and click **Print**, selecting your 80 mm thermal printer.
   Set the printer's paper size correctly. Booking tokens are separate from
   payment receipts and clearly identified as tokens.
7. Use **Today's queue** to see each service's daily sequence. Mark appointments
   arrived on their scheduled date. Rescheduling to another date preserves the
   old token and issues a new one. Cancellation never creates an automatic refund.

## Admin: refunds and corrections

- Open a booking's original collection and select **Refund**. Enter the amount,
  reason and method; digital refunds require a reference. Total refunds cannot
  exceed what remains refundable on that collection.
- **Correct** changes an unrefunded collection's payment method/reference. It
  posts a reversal and a replacement receipt for the same amount. The original
  is preserved and marked reversed. It cannot silently rewrite cash history.
- Only admin can refund, correct or approve discounts. Operator-collected amounts
  must equal the approved charge; arbitrary amount changes are not supported.

## Doctor: reports and revisions

1. Open a booking from **Bookings** or **Today's queue** and select **Write report**.
2. Choose an admin-created template for that service. Enter measurement values,
   review/edit findings and impression, and select structured diagnoses.
3. Save a draft or select **Finalize report for printing**. Drafts cannot print.
   Finalizing each service marks it complete; a booking completes after all its
   services have finalized reports.
4. Only the doctor can open the diagnostic print page and print A4 reports.
5. Revising a finalized report requires a reason. Each save creates a new
   preserved version. Revised reports are marked Revised; historical prints are
   marked Superseded when a newer finalized version exists.
6. Editing a template or patient record does not rewrite older report snapshots.

## Admin: reports and backups

- **Cash reports** show posted collections, refunds, correction reversals and net
  collection using transaction dates in Pakistan time. Net is collections minus
  refunds and reversals. Method totals distinguish cash from digital collection.
  User totals reflect the user who posted each entry, including admin reversals.
- **Patient reports** use visit dates and age at booking. Filter by service,
  structured diagnosis and referring doctor. Unique patients, visits and booked
  services are distinct counts. Disease analysis uses the latest finalized
  report per service; multiple diagnoses may each count the same visit.
- CSV exports are available for financial ledger and patient analysis.
- **Backups** downloads a verified database archive. Save it on your external
  drive and protect it. **Backup Clinic.bat** remembers an external folder and
  can be scheduled on the Windows host; it reports failure if the drive is absent.
- Restore into a separate database and inspect it before switching the clinic
  application. See [backup instructions](BACKUPS.md).

## Before real clinic use

Validate PostgreSQL with two operators, Windows host restart/LAN access, actual
80 mm and A4 printers, and a restore on the intended host. Configure daily backup
scheduling, retention and access protection. The software is a development build,
not yet a verified production clinic installation.
