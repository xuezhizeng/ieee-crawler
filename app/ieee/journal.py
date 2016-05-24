import math
import requests
from requests import Timeout
from pyquery import PyQuery
from app.models import Article
from mongoengine import DoesNotExist
from pymongo.errors import ServerSelectionTimeoutError
from app import logger
from app.ieee.citation import CitationLoader


class JournalCrawler:
    INIT_ARTICLE_PER_PAGE = 25

    def __init__(self, journal_number):
        self.__journal_number = str(journal_number)
        self.__current_issue_file = \
            'out/' + self.__journal_number + '_current_issue.txt'
        self.__early_access_file = \
            'out/' + self.__journal_number + '_early_access.txt'
        self.__new_article_file = \
            'out/' + self.__journal_number + '_new_articles.txt'

    def get_current_issue(self, to_file=False):
        url = 'http://ieeexplore.ieee.org/xpl/mostRecentIssue.jsp'
        numbers = self.get_article_numbers(url)
        return self.get_articles(
            numbers,
            self.__current_issue_file if to_file else None
        )

    def get_early_access(self, to_file=False):
        url = 'http://ieeexplore.ieee.org/xpl/tocresult.jsp'
        issue_number = self.get_early_access_number()
        numbers = self.get_article_numbers(url, issue_number=issue_number)
        return self.get_articles(
            numbers,
            self.__early_access_file if to_file else None
        )

    def get_new_articles(self, to_file=False):
        url = 'http://ieeexplore.ieee.org/xpl/tocresult.jsp'
        numbers = self.get_article_numbers(url, skip_exists=True)
        return self.get_articles(
            numbers,
            self.__new_article_file if to_file else None
        )

    def get_early_access_number(self):
        url = 'http://ieeexplore.ieee.org/xpl/RecentIssue.jsp'
        payload = {
            'punumber': self.__journal_number
        }
        r = requests.get(url, params=payload)

        query = PyQuery(r.text)
        issue_url = query('#nav-article li:eq(2) a').attr('href')
        return issue_url.split('=')[1]

    def get_article_numbers(self, url, issue_number=None, skip_exists=False):
        journal_number = self.__journal_number

        logger.info('Obtaining article numbers')

        payload = {
            'punumber': journal_number
        }

        if issue_number:
            payload['isnumber'] = issue_number

        r = None
        num_try = 1
        while True:
            try:
                logger.info('Page 1: Trying %d time(s)' % num_try)
                r = requests.get(url=url, params=payload)
                break
            except Timeout:
                num_try += 1
                if num_try > 10:
                    logger.info('Timeout')
                    break
        del num_try
        if not r:
            return []

        query = PyQuery(r.text)
        number_of_articles = self.__get_number_of_article(query)

        numbers = []

        for i in range(0, math.ceil(number_of_articles / self.INIT_ARTICLE_PER_PAGE)):
            if i > 0:
                payload['pageNumber'] = i + 1
                r = None
                num_try = 1
                while True:
                    try:
                        logger.info('Page %d: Trying %d time(s)' % (i + 1, num_try))
                        r = requests.get(url=url, params=payload)
                        break
                    except Timeout:
                        num_try += 1
                        if num_try > 10:
                            logger.info('Timeout')
                            break
                del num_try
                if not r:
                    continue
                query = PyQuery(r.text)

            elems = query('#results-blk .results li')

            tmp_numbers = [elem.attrib['aria-describedby'].split(' ')[0].split('-')[3]
                           for elem in elems]
            if skip_exists:
                for number in tmp_numbers:
                    try:
                        Article.objects.get(article_number=number)
                    except DoesNotExist:
                        numbers.append(number)
            else:
                numbers.extend(tmp_numbers)

        logger.info('Article numbers obtained: %d articles' % number_of_articles)

        return numbers

    @staticmethod
    def get_articles(numbers, filename=None):
        if filename:
            with open(filename, 'w') as fid:
                fid.write('')

        citation_loader = CitationLoader(numbers)
        entries = citation_loader.get_bibtex()
        articles = {}

        for entry in entries:
            number = entry['ID']

            try:
                article = Article.objects.get(entry_number=number)
                logger.info('Article [%s] already exists, it will be updated.' % number)
            except (DoesNotExist, ServerSelectionTimeoutError):
                article = Article()
                article.entry_number = number
                logger.info('Article [%s] is a new article.' % number)

            article.title = entry['title']
            article.author = entry['author']
            article.journal = entry['journal']
            article.year = entry['year']
            article.volume = entry['volume']
            article.number = entry['number']
            article.pages = entry['pages']
            article.abstract = entry['abstract']
            article.keyword = entry['keyword']
            article.doi = entry['doi']
            article.issn = entry['issn']

            try:
                article.save()
                logger.info('Article [%s] saved.' % number)
            except ServerSelectionTimeoutError:
                logger.info('Cannot connect to database, Article [%s] will not be saved.' % number)
            articles[number] = article

            if filename:
                with open(filename, 'a') as fid:
                    fid.write('Entry Number: %s\n' % number)
                    fid.write('Title: %s\n' % article.title)
                    fid.write('Author %s\n' % article.author)
                    fid.write('Abstract: %s\n' % article.abstract)
                    fid.write('Keyword: %s\n' % article.keyword)
                    fid.write('\n')

        return articles

    @staticmethod
    def __get_number_of_article(query):
        return int(query('#results-blk .results-display b:eq(1)').text())
