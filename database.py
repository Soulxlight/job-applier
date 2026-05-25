from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    company = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(200))
    salary = db.Column(db.String(100))
    url = db.Column(db.String(500), unique=True, nullable=False)
    description = db.Column(db.Text)
    platform = db.Column(db.String(50))  # linkedin, indeed, ziprecruiter, greenhouse, lever
    job_type = db.Column(db.String(50))  # full-time, part-time, contract, etc.
    remote = db.Column(db.Boolean, default=False)
    easy_apply = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(30), default='pending')  # pending, approved, rejected, applied, failed, skipped
    found_at = db.Column(db.DateTime, default=datetime.utcnow)
    applied_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    external_id = db.Column(db.String(200))  # platform-specific job ID


class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey('job.id'), nullable=False)
    job = db.relationship('Job', backref='application')
    cover_letter = db.Column(db.Text)
    resume_path = db.Column(db.String(500))
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(30), default='submitted')  # submitted, confirmed, rejected, interviewing
    notes = db.Column(db.Text)
    screenshot_path = db.Column(db.String(500))


class SearchConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    keywords = db.Column(db.String(500))
    location = db.Column(db.String(200))
    remote_only = db.Column(db.Boolean, default=False)
    blacklisted_companies = db.Column(db.Text)  # newline-separated
    min_salary = db.Column(db.Integer)
    job_types = db.Column(db.String(200))  # comma-separated
    platforms = db.Column(db.String(200))  # comma-separated
    auto_approve = db.Column(db.Boolean, default=False)
    max_applications_per_run = db.Column(db.Integer, default=10)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls):
        config = cls.query.first()
        if not config:
            config = cls(
                keywords='software engineer',
                location='',
                remote_only=False,
                blacklisted_companies='',
                job_types='full-time',
                platforms='linkedin,indeed,ziprecruiter',
                auto_approve=False,
                max_applications_per_run=10,
            )
            db.session.add(config)
            db.session.commit()
        return config
