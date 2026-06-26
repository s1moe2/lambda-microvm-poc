# Docker-in-Docker demo for a Lambda MicroVM.
# docker:dind provides dockerd + the docker CLI (Alpine userland); add python3 for the report server.
FROM docker:27-dind
RUN apk add --no-cache python3
COPY dind_report.py /dind_report.py
EXPOSE 8080
# We manage dockerd ourselves from the report server, so skip docker:dind's auto-start entrypoint.
ENTRYPOINT []
CMD ["python3", "/dind_report.py"]
