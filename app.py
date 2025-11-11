import json
import os
import smtplib
import uuid
from ics import Calendar, Event
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from datetime import date, timedelta, datetime
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.utils import secure_filename
from dateutil.relativedelta import relativedelta
from fpdf import FPDF

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Needed for flashing messages
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CERTIFICATE_FOLDER'] = 'certificates'

def load_documents():
    try:
        with open('data.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_documents(documents):
    with open('data.json', 'w') as f:
        json.dump(documents, f, indent=4)

def load_config():
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'auto_delete_expired': False}

def save_config(config):
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

def generate_certificate(doc):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", size=24)
    
    pdf.cell(200, 10, txt="Certificat d'Exposició Pública", ln=True, align='C')
    pdf.ln(10)
    
    pdf.set_font("Arial", size=12)
    pdf.multi_cell(0, 10, f"Aquest document certifica que el document: '{doc['name']}' ha estat en exposició pública a la web de INCASOL durant el següent període:")
    pdf.ln(5)

    pdf.cell(0, 10, f"Data d'inici: {doc['startDate']}", ln=True)
    pdf.cell(0, 10, f"Data de finalització: {doc['endDate']}", ln=True)
    pdf.cell(0, 10, f"Durada: {doc['duration']} {doc['durationType'].replace('_', ' ')}", ln=True)
    pdf.ln(10)

    pdf.cell(0, 10, f"Certificat generat el: {date.today().isoformat()}", ln=True)

    if not os.path.exists(app.config['CERTIFICATE_FOLDER']):
        os.makedirs(app.config['CERTIFICATE_FOLDER'])
        
    cert_filename = f"cert_{doc['name']}.pdf"
    pdf.output(os.path.join(app.config['CERTIFICATE_FOLDER'], cert_filename))
    print(f"Generated certificate: {cert_filename}")

def load_holidays_from_ics(town_name):
    holidays = set()
    if not town_name:
        return holidays
    
    file_path = os.path.join('calendars', f"{secure_filename(town_name)}.ics")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            c = Calendar(f.read())
            for event in c.events:
                # Ensure we handle all-day events correctly
                if event.all_day:
                    holidays.add(event.begin.date())
                else:
                    holidays.add(event.begin.datetime.date())
    except FileNotFoundError:
        pass  # No holidays file found for this town
    return holidays

def calculate_end_date(start_date, duration, duration_type, town_name):
    if duration_type == 'natural_days':
        return start_date + timedelta(days=duration)
    
    if duration_type == 'months':
        return start_date + relativedelta(months=duration)

    # Default to 'working_days'
    holidays_bcn = load_holidays_from_ics('Barcelona')  # General holidays
    holidays_town = load_holidays_from_ics(town_name)   # Town-specific holidays
    all_holidays = holidays_bcn.union(holidays_town)
    
    end_date = start_date
    days_added = 0
    while days_added < duration:
        end_date += timedelta(days=1)
        if end_date.weekday() < 5 and end_date not in all_holidays:  # Monday to Friday and not a holiday
            days_added += 1
    return end_date


# def send_email(subject, body, to_email):
#     sender_email = os.environ.get('')
#     password = os.environ.get('EMAIL_PASSWORD')
#     smtp_server = os.environ.get('smtp.gmail.com')
#     smtp_port = int(os.environ.get('SMTP_PORT', 587))

#     msg = MIMEText(body)
#     msg['Subject'] = subject
#     msg['From'] = sender_email
#     msg['To'] = to_email

#     try:
#         with smtplib.SMTP(smtp_server, smtp_port) as server:
#             server.starttls()
#             server.login(sender_email, password)
#             server.sendmail(sender_email, [to_email], msg.as_string())
#         print(f"Email sent to {to_email}")
#     except Exception as e:
#         print(f"Failed to send email: {e}")

def check_expired_documents():
    with app.app_context():
        config = load_config()
        documents = load_documents()
        today = date.today()
        
        docs_to_keep = []
        
        for doc in documents:
            end_date = date.fromisoformat(doc['endDate'])
            if end_date < today:
                print(f"Document expired: {doc['name']}")
                if config.get('auto_delete_expired', False):
                    generate_certificate(doc)
                    if doc.get('filename'):
                        try:
                            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], doc['filename']))
                            print(f"Deleted file: {doc['filename']}")
                        except FileNotFoundError:
                            print(f"File not found for deletion: {doc['filename']}")
                    # Don't add to docs_to_keep to effectively delete it
                else:
                    doc['status'] = 'Expired'
                    docs_to_keep.append(doc)
            else:
                doc['status'] = 'Active'
                docs_to_keep.append(doc)
        
        save_documents(docs_to_keep)

@app.route('/')
def index():
    documents = load_documents()
    today = date.today()
    expired_docs = []
    active_docs = []
    for doc in documents:
        end_date = date.fromisoformat(doc['endDate'])
        if end_date < today:
            doc['status'] = 'Expired'
            expired_docs.append(doc)
        else:
            doc['status'] = 'Active'
            active_docs.append(doc)
    
    # Combine lists for the main table, but keep expired ones separate for alerts
    all_documents = expired_docs + active_docs
    return render_template('index.html', documents=all_documents, alerts=expired_docs)

def get_available_calendars():
    if not os.path.exists('calendars'):
        return []
    return [os.path.splitext(f)[0] for f in os.listdir('calendars') if f.endswith('.ics')]

@app.route('/add', methods=['GET', 'POST'])
def add_document():
    calendars = get_available_calendars()
    if request.method == 'POST':
        name = request.form['name']
        duration = int(request.form['duration'])
        duration_type = request.form['durationType']
        town = request.form['town']
        startDate = date.today()
        endDate = calculate_end_date(startDate, duration, duration_type, town)
        
        doc_id = str(uuid.uuid4())
        filename = None

        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename != '' and file.filename.endswith('.pdf'):
                filename = doc_id + '.pdf'
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        documents = load_documents()
        documents.append({
            'id': doc_id,
            'name': name,
            'startDate': startDate.isoformat(),
            'duration': duration,
            'durationType': duration_type,
            'endDate': endDate.isoformat(),
            'status': 'Active',
            'town': town,
            'filename': filename
        })
        save_documents(documents)
        return redirect(url_for('index'))
    return render_template('add_document.html', calendars=calendars)

@app.route('/delete/<doc_id>')
def delete_document(doc_id):
    documents = load_documents()
    
    doc_to_delete = next((doc for doc in documents if doc.get('id') == doc_id), None)
    
    if doc_to_delete:
        generate_certificate(doc_to_delete)
        if doc_to_delete.get('filename'):
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], doc_to_delete['filename']))
            except FileNotFoundError:
                pass # File was already deleted or never existed

    documents = [doc for doc in documents if doc.get('id') != doc_id]
    save_documents(documents)
    flash('Document deleted and certificate generated.')
    return redirect(url_for('index'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/upload-holidays', methods=['GET', 'POST'])
def upload_holidays():
    if request.method == 'POST':
        if 'file' not in request.files or 'town' not in request.form:
            flash('No file or town name provided.')
            return redirect(request.url)
        
        file = request.files['file']
        town = request.form['town']

        if file.filename == '' or town == '':
            flash('No selected file or town name.')
            return redirect(request.url)

        if file and file.filename.endswith('.ics'):
            if not os.path.exists('calendars'):
                os.makedirs('calendars')
            
            filename = secure_filename(town) + '.ics'
            file.save(os.path.join('calendars', filename))
            flash(f'Calendar for {town} uploaded successfully.')
            return redirect(url_for('manage_calendars'))
            
    return render_template('upload_holidays.html')

@app.route('/manage-calendars')
def manage_calendars():
    calendars = get_available_calendars()
    return render_template('manage_calendars.html', calendars=calendars)

@app.route('/create-calendar', methods=['POST'])
def create_calendar():
    town = request.form['town']
    if not town:
        flash('Town name cannot be empty.')
        return redirect(url_for('manage_calendars'))

    filename = secure_filename(town) + '.ics'
    file_path = os.path.join('calendars', filename)

    if not os.path.exists('calendars'):
        os.makedirs('calendars')

    if not os.path.exists(file_path):
        c = Calendar()
        with open(file_path, 'w') as f:
            f.write(str(c))
        flash(f'Calendar for {town} created successfully.')
    else:
        flash(f'Calendar for {town} already exists.')

    return redirect(url_for('manage_calendars'))

@app.route('/edit-calendar/<town_name>')
def edit_calendar(town_name):
    file_path = os.path.join('calendars', f"{secure_filename(town_name)}.ics")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            c = Calendar(f.read())
    except FileNotFoundError:
        flash(f'Calendar for {town_name} not found.')
        return redirect(url_for('manage_calendars'))
    
    # Sort events by date
    sorted_events = sorted(c.events, key=lambda e: e.begin)
    return render_template('edit_calendar.html', town_name=town_name, holidays=sorted_events)

@app.route('/add-holiday/<town_name>', methods=['POST'])
def add_holiday(town_name):
    holiday_date_str = request.form['holiday_date']
    holiday_name = request.form['holiday_name']
    
    if not holiday_date_str or not holiday_name:
        flash('Date and description are required.')
        return redirect(url_for('edit_calendar', town_name=town_name))

    file_path = os.path.join('calendars', f"{secure_filename(town_name)}.ics")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            c = Calendar(f.read())
    except FileNotFoundError:
        c = Calendar()

    e = Event()
    e.name = holiday_name
    e.begin = holiday_date_str
    e.make_all_day()
    c.events.add(e)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(str(c))

    flash('Holiday added successfully.')
    return redirect(url_for('edit_calendar', town_name=town_name))

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    config = load_config()
    if request.method == 'POST':
        config['auto_delete_expired'] = 'auto_delete' in request.form
        save_config(config)
        flash('Settings saved successfully.')
        return redirect(url_for('settings'))
    
    return render_template('settings.html', auto_delete_expired=config.get('auto_delete_expired', False))

@app.route('/delete-holiday/<town_name>/<holiday_uid>')
def delete_holiday(town_name, holiday_uid):
    file_path = os.path.join('calendars', f"{secure_filename(town_name)}.ics")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            c = Calendar(f.read())
    except FileNotFoundError:
        flash('Calendar not found.')
        return redirect(url_for('manage_calendars'))

    # Find and remove the event
    event_to_remove = None
    for event in c.events:
        if event.uid == holiday_uid:
            event_to_remove = event
            break
    
    if event_to_remove:
        c.events.remove(event_to_remove)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(str(c))
        flash('Holiday deleted successfully.')
    else:
        flash('Holiday not found.')

    return redirect(url_for('edit_calendar', town_name=town_name))

if __name__ == '__main__':
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_expired_documents, trigger="interval", seconds=30)
    scheduler.start()
    app.run(debug=True)
